"""RTSA-branded HTML landing page served at ``/``.

Colours are taken from the live website (rtsa.org.zm, "Grow News" theme):
gold accent ``#DDAF4D`` and slate primary ``#36454F``.
"""

from fastapi.responses import HTMLResponse

RTSA_GOLD = "#DDAF4D"
RTSA_SLATE = "#36454F"
RTSA_DARK = "#232323"

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{
    --slate: #36454F;
    --gold: #DDAF4D;
    --dark: #232323;
    --grey: #777777;
    --line: #E9E9E9;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: Raleway, Helvetica, Arial, sans-serif;
    color: var(--grey);
    background: #fff;
    line-height: 1.8;
  }}
  header {{
    background: var(--slate);
    border-top: 6px solid var(--gold);
    color: #fff;
    padding: 42px 20px 30px;
  }}
  header .inner {{ max-width: 920px; margin: 0 auto; }}
  header h1 {{ font-size: 30px; font-weight: 400; color: #fff; }}
  header p {{ color: #E3E3E3; font-size: 15px; margin-top: 6px; }}
  .badge {{
    display: inline-block;
    background: var(--gold);
    color: var(--dark);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 1px;
    text-transform: uppercase;
    padding: 3px 12px;
    border-radius: 2px;
    margin-bottom: 14px;
  }}
  main {{ max-width: 920px; margin: 0 auto; padding: 30px 20px 60px; }}
  h2 {{
    font-size: 18px;
    color: var(--slate);
    border-left: 4px solid var(--gold);
    padding-left: 12px;
    margin: 32px 0 14px;
  }}
  ul.links {{ list-style: none; }}
  ul.links li {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    border: 1px solid var(--line);
    border-radius: 3px;
    padding: 10px 16px;
    margin-bottom: 8px;
  }}
  ul.links a {{
    color: var(--slate);
    text-decoration: none;
    font-weight: 600;
    font-size: 14px;
  }}
  ul.links a:hover {{ color: var(--gold); }}
  ul.links code {{ font-size: 12px; color: #AAA; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }}
  .card {{
    border: 1px solid var(--line);
    border-top: 3px solid var(--gold);
    border-radius: 3px;
    padding: 16px;
  }}
  .card h3 {{ font-size: 14px; color: var(--dark); margin-bottom: 4px; }}
  .card p {{ font-size: 13px; }}
  .card code {{
    font-size: 12px;
    color: var(--slate);
    background: #F8F8F8;
    border: 1px solid var(--line);
    padding: 1px 6px;
    border-radius: 2px;
  }}
  footer {{
    background: var(--dark);
    color: #B3B3B3;
    font-size: 13px;
    padding: 18px 20px;
    text-align: center;
  }}
</style>
</head>
<body>
  <header>
    <div class="inner">
      <span class="badge">RTSA — Zambia</span>
      <h1>{title}</h1>
      <p>Vehicle registration, driver licensing, enforcement, toll compliance, road alerts and route planning.</p>
    </div>
  </header>

  <main>
    <h2>Explore the API</h2>
    <ul class="links">
      <li><a href="/docs">Interactive documentation</a> <code>/docs</code></li>
      <li><a href="/redoc">ReDoc reference</a> <code>/redoc</code></li>
      <li><a href="/openapi.json">OpenAPI schema</a> <code>/openapi.json</code></li>
      <li><a href="/api/v1/health">Health check</a> <code>/api/v1/health</code></li>
    </ul>

    <h2>Capabilities</h2>
    <div class="cards">
      <div class="card">
        <h3>Routing &amp; Alerts</h3>
        <p>Incident-aware route planning with alternative suggestions.</p>
        <code>/api/v1/routing</code>
      </div>
      <div class="card">
        <h3>Road Network</h3>
        <p>Intersections, roads and segments with GeoJSON and map views.</p>
        <code>/api/v1/road-network</code>
      </div>
      <div class="card">
        <h3>Driver Portal</h3>
        <p>Licence status, fines and online payment for citizens.</p>
        <code>/api/v1/portal</code>
      </div>
      <div class="card">
        <h3>Enforcement</h3>
        <p>Inspections, challans, tolls and ANPR field operations.</p>
        <code>/api/v1/enforcement</code>
      </div>
    </div>
  </main>

  <footer>RTSA ITMS {version} — Integrated Transport Management System</footer>
</body>
</html>
"""


def landing_page() -> HTMLResponse:
    return HTMLResponse(
        _PAGE.format(title="RTSA Integrated Transport Management System", version="0.8.0")
    )