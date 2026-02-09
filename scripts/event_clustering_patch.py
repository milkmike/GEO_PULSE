#!/usr/bin/env python3
"""
Patch 1: Add event_key to analysis prompt (v1.6)
Patch 2: Update temperature formula to cluster by event_key
Patch 3: Add event_key column to analysis table
"""

import psycopg2

DB_PARAMS = {
    "host": "db", "port": 5432,
    "dbname": "cis_thermometer", "user": "thermo",
    "password": "oIa-pEx6jdhrjZK4AZWqUpvrKNT5GYt7",
}


def patch_db():
    """Add event_key column to analysis table."""
    conn = psycopg2.connect(**DB_PARAMS)
    cur = conn.cursor()
    
    # Check if column exists
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_name = 'analysis' AND column_name = 'event_key'
    """)
    if cur.fetchone():
        print("DB: event_key column already exists")
    else:
        cur.execute("ALTER TABLE analysis ADD COLUMN event_key VARCHAR(200)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_analysis_event_key ON analysis(event_key)")
        conn.commit()
        print("DB: event_key column + index added")
    
    cur.close()
    conn.close()


def patch_prompt():
    """Update prompt to v1.6 with event_key field."""
    filepath = '/opt/cis-thermometer/src/pipeline/prompts.py'
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Update version
    content = content.replace('PROMPT_VERSION = "v1.5"', 'PROMPT_VERSION = "v1.6"')
    
    # Add event_key to JSON output
    old_json = '{"is_relevant": true/false, "sentiment": N, "action_level": N, "confidence": 0.0-1.0, "event_type": "...", "reasoning": "1 предложение"}'
    new_json = '{"is_relevant": true/false, "sentiment": N, "action_level": N, "confidence": 0.0-1.0, "event_type": "...", "event_key": "краткий ID события (3-5 слов)", "reasoning": "1 предложение"}'
    
    if old_json in content:
        content = content.replace(old_json, new_json)
        print("Prompt: JSON output updated with event_key")
    else:
        print("WARNING: JSON pattern not found in prompt")
    
    # Add event_key explanation before the JSON line
    old_event_type = '3. event_type: diplomatic | military | economic | cultural | security'
    new_event_type = """3. event_type: diplomatic | military | economic | cultural | security

4. event_key: краткий идентификатор СОБЫТИЯ (3-5 слов, lowercase, без дат)
   Все статьи об ОДНОМ событии должны получить ОДИНАКОВЫЙ event_key.
   Примеры:
   - "запрет въезда сабурову в россию" (все статьи про Сабурова → один ключ)
   - "выход армении из одкб" (все статьи про выход → один ключ)
   - "газовые переговоры казахстан россия" (все про эти переговоры → один ключ)
   Цель: группировка статей об одном событии для устранения медийного шума."""
    
    if old_event_type in content:
        content = content.replace(old_event_type, new_event_type)
        print("Prompt: event_key explanation added")
    else:
        print("WARNING: event_type pattern not found")
    
    with open(filepath, 'w') as f:
        f.write(content)
    print(f"Prompt: saved as v1.6")


def patch_sentiment_parser():
    """Update sentiment.py to extract and save event_key."""
    filepath = '/opt/cis-thermometer/src/pipeline/sentiment.py'
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Add event_key to the fields being extracted
    if 'event_key' not in content:
        # Find where event_type is extracted and add event_key
        old = '"event_type": data.get("event_type")'
        new = '"event_type": data.get("event_type"),\n                "event_key": data.get("event_key")'
        if old in content:
            content = content.replace(old, new)
            print("Sentiment parser: event_key extraction added")
        else:
            print("WARNING: event_type extraction pattern not found in sentiment.py")
            # Try alternative patterns
            if 'event_type' in content:
                print("  event_type IS in file, just different pattern")
                # Show context
                idx = content.find('event_type')
                print(f"  Context: {content[max(0,idx-50):idx+100]}")
    
    with open(filepath, 'w') as f:
        f.write(content)


def patch_temperature():
    """Update temperature formula to cluster by event_key."""
    filepath = '/opt/cis-thermometer/src/engine/index.py'
    with open(filepath, 'r') as f:
        content = f.read()
    
    # Add event_key to the SQL query
    old_select = """SELECT a.sentiment, a.event_type, a.sentiment_confidence,
                       a.action_level,
                       ar.published_at, s.weight, s.id as source_id,
                       COALESCE(ar.reprint_count, 0) as reprint_count"""
    new_select = """SELECT a.sentiment, a.event_type, a.sentiment_confidence,
                       a.action_level, a.event_key,
                       ar.published_at, s.weight, s.id as source_id,
                       COALESCE(ar.reprint_count, 0) as reprint_count"""
    
    if old_select in content:
        content = content.replace(old_select, new_select)
        print("Temperature: event_key added to SQL query")
    else:
        print("WARNING: SQL select pattern not found")
    
    # Replace the main loop with event-clustered version
    old_loop_start = """        for row in rows:
            sentiment = float(row.sentiment)
            published_at = row.published_at
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            
            age = (now - published_at).total_seconds()
            decay = math.exp(-age / TAU)
            
            w_source = float(row.weight or 1.0)
            event_type = row.event_type
            w_type = EVENT_TYPE_WEIGHTS.get(event_type, 1.0)
            
            importance = 1 + math.log1p(row.reprint_count)
            
            # Action level multiplier
            action_mult = ACTION_MULTIPLIERS.get(row.action_level or 1, 1)
            
            weight = w_source * w_type * decay * importance * action_mult
            numerator += sentiment * weight
            denominator += abs(weight)
            
            if event_type in type_sums:
                type_sums[event_type] += sentiment
                type_counts[event_type] += 1
            
            source_ids.add(row.source_id)"""
    
    new_loop = """        # Phase 1: Group articles by event_key
        event_clusters = {}  # event_key -> list of rows
        unclustered = []     # articles without event_key
        
        for row in rows:
            ek = getattr(row, 'event_key', None)
            if ek and len(ek) > 3:
                event_clusters.setdefault(ek, []).append(row)
            else:
                unclustered.append(row)
        
        # Phase 2: For each cluster, pick best article (highest source weight)
        # and apply diminishing returns for additional coverage
        clustered_rows = []
        for ek, cluster in event_clusters.items():
            # Sort by source weight descending
            cluster.sort(key=lambda r: float(r.weight or 1.0), reverse=True)
            for i, row in enumerate(cluster):
                # First article: full weight. Each subsequent: 20% of previous
                clustered_rows.append((row, 0.2 ** i))
        
        # Unclustered articles get full weight
        for row in unclustered:
            clustered_rows.append((row, 1.0))
        
        for row, cluster_decay in clustered_rows:
            sentiment = float(row.sentiment)
            published_at = row.published_at
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            
            age = (now - published_at).total_seconds()
            decay = math.exp(-age / TAU)
            
            w_source = float(row.weight or 1.0)
            event_type = row.event_type
            w_type = EVENT_TYPE_WEIGHTS.get(event_type, 1.0)
            
            importance = 1 + math.log1p(row.reprint_count)
            
            # Action level multiplier
            action_mult = ACTION_MULTIPLIERS.get(row.action_level or 1, 1)
            
            # cluster_decay: 1.0 for first/only article, 0.2^n for duplicates
            weight = w_source * w_type * decay * importance * action_mult * cluster_decay
            numerator += sentiment * weight
            denominator += abs(weight)
            
            if event_type in type_sums:
                type_sums[event_type] += sentiment * cluster_decay
                type_counts[event_type] += cluster_decay
            
            source_ids.add(row.source_id)"""
    
    if old_loop_start in content:
        content = content.replace(old_loop_start, new_loop)
        print("Temperature: event clustering loop applied")
    else:
        print("WARNING: main loop pattern not found")
    
    with open(filepath, 'w') as f:
        f.write(content)


if __name__ == "__main__":
    print("=" * 50)
    print("Event Clustering Patch")
    print("=" * 50)
    
    print("\n1. Database...")
    patch_db()
    
    print("\n2. Prompt...")
    patch_prompt()
    
    print("\n3. Sentiment parser...")
    patch_sentiment_parser()
    
    print("\n4. Temperature formula...")
    patch_temperature()
    
    print("\n" + "=" * 50)
    print("DONE! Restart analyzer + temperature services.")
