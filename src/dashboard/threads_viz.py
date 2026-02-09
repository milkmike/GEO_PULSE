"""Threads/Bonds visualization — pure SVG, no D3 dependency."""
import json
import math

FLAGS = {
    'KZ': '🇰🇿', 'AM': '🇦🇲', 'UZ': '🇺🇿', 'KG': '🇰🇬',
    'TJ': '🇹🇯', 'TM': '🇹🇲', 'AZ': '🇦🇿', 'GE': '🇬🇪',
    'MD': '🇲🇩', 'BY': '🇧🇾',
}


def _temp_color(t):
    if t >= 20: return '#22c55e'
    if t >= 5: return '#86efac'
    if t >= -5: return '#fbbf24'
    if t >= -20: return '#f97316'
    if t >= -40: return '#ef4444'
    return '#991b1b'


def _line_style(t):
    if t < -35:
        return 'stroke-dasharray: 8 6;'
    return ''


def get_threads_html(countries_data: list) -> str:
    W, H = 900, 600
    CX, CY = W // 2, H // 2
    R = 220  # radius for country nodes

    # Position countries in a circle around Russia
    n = len(countries_data)
    positions = {}
    for i, c in enumerate(countries_data):
        angle = (2 * math.pi * i / n) - math.pi / 2
        x = CX + R * math.cos(angle)
        y = CY + R * math.sin(angle)
        positions[c['code']] = (x, y)

    # Build SVG
    lines_svg = ''
    nodes_svg = ''

    # Lines from Russia to each country
    for c in countries_data:
        temp = c.get('temperature', 0)
        x, y = positions[c['code']]
        color = _temp_color(temp)
        width = max(1.5, min(5, 3 + temp / 20))
        style = _line_style(temp)
        opacity = 0.7 if temp > -35 else 0.4

        lines_svg += f'''
        <line x1="{CX}" y1="{CY}" x2="{x:.0f}" y2="{y:.0f}"
              stroke="{color}" stroke-width="{width:.1f}"
              opacity="{opacity}" style="{style}"
              class="thread-line"/>
        '''

    # Russia center node
    nodes_svg += f'''
    <circle cx="{CX}" cy="{CY}" r="36" fill="#1e293b" stroke="#475569" stroke-width="2"/>
    <text x="{CX}" y="{CY-8}" text-anchor="middle" fill="white" font-size="22">🇷🇺</text>
    <text x="{CX}" y="{CY+16}" text-anchor="middle" fill="#94a3b8" font-size="10" font-weight="600">РОССИЯ</text>
    '''

    # Country nodes
    for c in countries_data:
        x, y = positions[c['code']]
        temp = c.get('temperature', 0)
        color = _temp_color(temp)
        flag = FLAGS.get(c['code'], '🏳')
        sign = '+' if temp > 0 else ''
        trend_arrow = {'rising': '↗', 'falling': '↘', 'stable': '→'}.get(c.get('trend', 'stable'), '→')
        events_dots = ''
        for i, ev in enumerate(c.get('events', [])[:3]):
            al = ev.get('action_level', 1)
            ev_color = '#ef4444' if al >= 4 else '#f97316' if al >= 2 else '#fbbf24'
            events_dots += f'<circle cx="{x - 12 + i*12}" cy="{y + 38}" r="3" fill="{ev_color}"/>'

        nodes_svg += f'''
        <g class="country-node" data-code="{c['code']}">
            <circle cx="{x:.0f}" cy="{y:.0f}" r="30" fill="#0f172a" stroke="{color}"
                    stroke-width="2" class="node-circle"/>
            <text x="{x:.0f}" y="{y-5:.0f}" text-anchor="middle" fill="white" font-size="18">{flag}</text>
            <text x="{x:.0f}" y="{y+12:.0f}" text-anchor="middle" fill="{color}"
                  font-size="11" font-weight="700">{sign}{temp:.0f}°</text>
            <text x="{x:.0f}" y="{y+26:.0f}" text-anchor="middle" fill="#64748b"
                  font-size="9" font-weight="500">{c.get('name', c['code'])} {trend_arrow}</text>
            {events_dots}
        </g>
        '''

    # Tooltip overlay
    tooltip = '<div id="tooltip" style="display:none;position:absolute;background:#1e293b;border:1px solid rgba(255,255,255,0.1);border-radius:8px;padding:12px;font-size:12px;color:#e2e8f0;pointer-events:none;z-index:10"></div>'

    html = f'''<!DOCTYPE html>
<html>
<head>
<style>
    body {{ margin: 0; background: #0a0a0f; overflow: hidden; font-family: Inter, system-ui, sans-serif; }}
    svg {{ display: block; }}
    .thread-line {{
        filter: drop-shadow(0 0 4px currentColor);
    }}
    .node-circle {{
        filter: drop-shadow(0 0 8px rgba(255,255,255,0.1));
        transition: all 0.3s;
    }}
    .country-node:hover .node-circle {{
        stroke-width: 3;
        filter: drop-shadow(0 0 16px rgba(255,255,255,0.2));
    }}
    @keyframes pulse {{
        0%, 100% {{ opacity: 0.6; }}
        50% {{ opacity: 1; }}
    }}
    .thread-line {{
        animation: pulse 3s ease-in-out infinite;
    }}

    /* Legend */
    .legend {{
        position: absolute;
        bottom: 10px;
        left: 50%;
        transform: translateX(-50%);
        display: flex;
        gap: 20px;
        font-size: 11px;
        color: #64748b;
    }}
    .legend-item {{
        display: flex;
        align-items: center;
        gap: 6px;
    }}
    .legend-line {{
        width: 24px;
        height: 3px;
        border-radius: 2px;
    }}
</style>
</head>
<body>
    <svg viewBox="0 0 {W} {H}" width="100%" height="100%">
        <defs>
            <radialGradient id="bg-glow">
                <stop offset="0%" stop-color="#1e293b" stop-opacity="0.3"/>
                <stop offset="100%" stop-color="#0a0a0f" stop-opacity="0"/>
            </radialGradient>
        </defs>
        <circle cx="{CX}" cy="{CY}" r="280" fill="url(#bg-glow)"/>
        {lines_svg}
        {nodes_svg}
    </svg>

    <div class="legend">
        <div class="legend-item"><div class="legend-line" style="background:#22c55e"></div> Позитив</div>
        <div class="legend-item"><div class="legend-line" style="background:#fbbf24"></div> Нейтрально</div>
        <div class="legend-item"><div class="legend-line" style="background:#ef4444"></div> Негатив</div>
        <div class="legend-item"><div class="legend-line" style="background:#ef4444;border-style:dashed"></div> Разрыв</div>
    </div>
</body>
</html>'''

    return html
