"""RTSA-branded HTML landing page served at ``/about``.

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
  @font-face {{
    font-family: "Inter";
    font-style: normal; font-weight: 400; font-display: swap;
    src: url("/static/fonts/inter-latin-400-normal.woff2") format("woff2");
  }}
  @font-face {{
    font-family: "Inter";
    font-style: normal; font-weight: 500; font-display: swap;
    src: url("/static/fonts/inter-latin-500-normal.woff2") format("woff2");
  }}
  @font-face {{
    font-family: "Inter";
    font-style: normal; font-weight: 600; font-display: swap;
    src: url("/static/fonts/inter-latin-600-normal.woff2") format("woff2");
  }}
  @font-face {{
    font-family: "Inter";
    font-style: normal; font-weight: 700; font-display: swap;
    src: url("/static/fonts/inter-latin-700-normal.woff2") format("woff2");
  }}
  :root {{
    --slate: #36454F; --gold: #DDAF4D; --gold-strong: #c69a34;
    --dark: #232323; --grey: #5a6570; --line: #e3e7ec; --bg: #f3f4f6;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: Inter, "Segoe UI", system-ui, Arial, sans-serif;
    color: var(--grey);
    background: var(--bg);
    line-height: 1.7;
    -webkit-font-smoothing: antialiased;
  }}
  ::selection {{ background: #f4e9cf; color: var(--dark); }}
  header {{
    background:
      radial-gradient(900px 460px at 88% -20%, rgba(221, 175, 77, 0.25), transparent 60%),
      var(--slate);
    border-bottom: 4px solid var(--gold);
    color: #fff;
    padding: 56px 20px 46px;
  }}
  header .inner {{ max-width: 960px; margin: 0 auto; }}
  .wordmark {{ display: flex; align-items: center; gap: 14px; margin-bottom: 18px; }}
  .mark {{
    width: 46px; height: 46px;
    background: linear-gradient(135deg, #43576a, #232a30);
    border-radius: 12px;
    display: grid; place-items: center;
    font-weight: 700; font-size: 22px; color: var(--gold);
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.14);
  }}
  .badge {{
    display: inline-block;
    background: var(--gold);
    color: var(--dark);
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 1.3px;
    text-transform: uppercase;
    padding: 4px 14px;
    border-radius: 999px;
    margin-bottom: 18px;
  }}
  header h1 {{ font-size: 34px; font-weight: 700; letter-spacing: -0.015em; color: #fff; }}
  header p {{ color: #e3e8ec; font-size: 15.5px; margin-top: 8px; max-width: 620px; }}
  main {{ max-width: 960px; margin: 0 auto; padding: 34px 20px 70px; }}
  .row {{ display: flex; gap: 22px; flex-wrap: wrap; }}
  .row > div {{ flex: 1 1 380px; }}
  h2 {{
    font-size: 18px;
    color: var(--slate);
    border-left: 4px solid var(--gold);
    padding-left: 12px;
    margin: 10px 0 16px;
  }}
  form#login {{
    background: #fff;
    border: 1px solid var(--line);
    border-radius: 14px;
    box-shadow: 0 1px 2px rgba(54,69,79,.06), 0 8px 24px -6px rgba(54,69,79,.12);
    padding: 22px;
  }}
  form#login label {{ display: block; font-size: 13px; color: var(--dark); font-weight: 600; margin: 12px 0 5px; }}
  form#login input {{
    width: 100%;
    border: 1px solid #cbd3db;
    border-radius: 9px;
    padding: 10px 12px;
    font-size: 14px;
    font-family: inherit;
    color: var(--slate);
  }}
  form#login input:focus {{ outline: none; border-color: var(--gold); box-shadow: 0 0 0 3px rgba(221, 175, 77, 0.35); }}
  form#login button {{
    margin-top: 16px;
    width: 100%;
    background: var(--gold);
    color: #3a2d10;
    border: none;
    border-radius: 9px;
    padding: 11px;
    font-size: 14px;
    font-weight: 700;
    cursor: pointer;
    transition: background .15s ease, transform .08s ease;
  }}
  form#login button:hover {{ background: var(--gold-strong); }}
  form#login button:active {{ transform: translateY(1px) scale(.99); }}
  #login-msg {{ font-size: 13px; min-height: 20px; margin-top: 10px; color: #5a6570; }}
  #login-msg.ok {{ color: #17734f; }}
  #login-msg.err {{ color: #b3372b; }}
  .hint {{ font-size: 12.5px; color: #8a94a0; margin-top: 12px; line-height: 1.8; }}
  .hint code {{
    font-family: "JetBrains Mono", Consolas, monospace; font-size: 12px;
    background: #f3f4f6; border: 1px solid var(--line);
    padding: 1px 6px; border-radius: 5px; color: var(--slate);
  }}
  ul.links {{ list-style: none; }}
  ul.links li {{
    background: #fff;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border: 1px solid var(--line);
    border-radius: 10px;
    padding: 12px 16px;
    margin-bottom: 8px;
    box-shadow: 0 1px 2px rgba(54,69,79,.05);
  }}
  ul.links a {{ color: var(--slate); text-decoration: none; font-weight: 600; font-size: 14px; }}
  ul.links a:hover {{ color: var(--gold-strong); }}
  ul.links code {{ font-size: 12px; color: #8a94a0; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 14px; }}
  .card {{
    background: #fff;
    border: 1px solid var(--line);
    border-top: 3px solid var(--gold);
    border-radius: 14px;
    padding: 18px;
    box-shadow: 0 1px 2px rgba(54,69,79,.05);
  }}
  .card h3 {{ font-size: 14px; color: var(--dark); margin-bottom: 4px; }}
  .card p {{ font-size: 13px; }}
  .card code {{
    font-family: "JetBrains Mono", Consolas, monospace;
    font-size: 12px; color: var(--slate);
    background: #f3f4f6; border: 1px solid var(--line);
    padding: 1px 7px; border-radius: 5px;
  }}
  footer {{
    background: var(--dark);
    border-top: 3px solid var(--gold);
    color: #b3b3b3;
    font-size: 13px;
    padding: 20px;
    text-align: center;
  }}
  @media (max-width: 640px) {{
    header {{ padding: 40px 16px 34px; }}
    header h1 {{ font-size: 26px; }}
    main {{ padding: 24px 16px 50px; }}
  }}
</style>
</head>
<body>
  <header>
    <div class="inner">
      <div class="wordmark">
        <div class="mark" aria-hidden="true">R</div>
        <span class="badge">RTSA — Zambia</span>
      </div>
      <h1>{title}</h1>
      <p>Vehicle registration, driver licensing, enforcement, toll compliance, road alerts and route planning.</p>
    </div>
  </header>

  <main>
    <div class="row">
      <div>
        <h2>Sign in</h2>
        <form id="login" onsubmit="return doLogin(event)">
          <label for="email">Email</label>
          <input id="email" type="email" autocomplete="username" placeholder="admin@rtsa.gov.zm">
          <label for="password">Password</label>
          <input id="password" type="password" autocomplete="current-password" placeholder="&bull;&bull;&bull;&bull;&bull;&bull;&bull;&bull;">
          <button type="submit">Sign in</button>
          <div id="login-msg"></div>
          <div class="hint">Demo accounts: <code>admin@rtsa.gov.zm / admin123</code> &middot; <code>officer@rtsa.gov.zm / officer123</code> &middot; <code>citizen@example.com / citizen123</code></div>
        </form>
      </div>
      <div>
        <h2>Explore the platform</h2>
        <ul class="links">
          <li><a href="/">Open the Web App</a> <code>/</code></li>
          <li><a href="/docs">Interactive documentation</a> <code>/docs</code></li>
          <li><a href="/redoc">ReDoc reference</a> <code>/redoc</code></li>
          <li><a href="/openapi.json">OpenAPI schema</a> <code>/openapi.json</code></li>
          <li><a href="/health">Health check</a> <code>/health</code></li>
        </ul>
      </div>
    </div>

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
  <script>
    async function doLogin(event) {{
      event.preventDefault();
      var msg = document.getElementById('login-msg');
      msg.className = '';
      msg.textContent = 'Signing in…';
      try {{
        var res = await fetch('/api/auth/login', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{
            email: document.getElementById('email').value,
            password: document.getElementById('password').value
          }})
        }});
        var data = await res.json();
        if (!res.ok) throw new Error(data.detail || ('HTTP ' + res.status));
        window.localStorage.setItem('rtsa_token', data.access_token);
        msg.className = 'ok';
        msg.textContent = 'Signed in. Opening the web app…';
        setTimeout(function () {{ window.location.href = '/'; }}, 500);
      }} catch (err) {{
        msg.className = 'err';
        msg.textContent = 'Login failed: ' + err.message;
      }}
      return false;
    }}
  </script>
</body>
</html>
"""


def landing_page() -> HTMLResponse:
    return HTMLResponse(
        _PAGE.format(title="RTSA Integrated Transport Management System", version="0.8.0")
    )