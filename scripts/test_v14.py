"""Test prompt v1.4 on two articles."""
import json
import httpx
import os
from src.pipeline.prompts import SENTIMENT_PROMPT, PROMPT_VERSION
from src.config import COUNTRY_NAMES

key = os.environ['OPENROUTER_API_KEY']
tests = [
    ('Посол России в Азербайджане вызван в МИД: Москве передана нота протеста в связи с заявлением Затулина', 'Азербайджан'),
    ('Азербайджанский ученый удостоена награды международного конкурса в Москве', 'Азербайджан'),
]

print(f"Testing prompt {PROMPT_VERSION}")
for title, country in tests:
    prompt = SENTIMENT_PROMPT.format(country=country, source='test', title=title, body=title)
    r = httpx.post('https://openrouter.ai/api/v1/chat/completions',
        headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
        json={'model': 'anthropic/claude-sonnet-4', 'messages': [{'role': 'user', 'content': prompt}], 'max_tokens': 300, 'temperature': 0},
        timeout=30)
    content = r.json()['choices'][0]['message']['content'].strip()
    if content.startswith('```'):
        content = content.split('\n', 1)[1] if '\n' in content else content[3:]
    if content.endswith('```'):
        content = content[:-3]
    result = json.loads(content.strip())
    print(f"\n{title[:70]}...")
    print(f"  sentiment={result.get('sentiment')}, action_level={result.get('action_level')}, relevant={result.get('is_relevant')}")
