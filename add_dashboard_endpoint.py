"""Voeg developer dashboard endpoint toe"""

with open('backend/main.py', 'r') as f:
    content = f.read()

# Check of het al bestaat
if 'developer_dashboard.html' in content:
    print("Dashboard endpoint al toegevoegd!")
else:
    # Voeg HTMLResponse import toe als die niet bestaat
    if 'HTMLResponse' not in content:
        content = content.replace(
            'from fastapi import FastAPI, HTTPException, Request',
            'from fastapi import FastAPI, HTTPException, Request\nfrom fastapi.responses import HTMLResponse'
        )
    
    # Voeg dashboard endpoint toe voor de health endpoint
    dashboard_code = '''
@app.get("/developers", response_class=HTMLResponse)
async def developer_dashboard():
    """Developer dashboard pagina."""
    with open("backend/developer_dashboard.html", "r") as f:
        return HTMLResponse(content=f.read(), status_code=200)
'''
    
    # Voeg toe na de health endpoint
    if '@app.get("/health")' in content:
        content = content.replace(
            '@app.get("/health")',
            dashboard_code + '\n@app.get("/health")'
        )
    
    with open('backend/main.py', 'w') as f:
        f.write(content)
    
    print("✅ Dashboard endpoint toegevoegd!")

# Verifieer
with open('backend/main.py', 'r') as f:
    if 'developer_dashboard' in f.read():
        print("✅ Verifieerd: dashboard endpoint staat in main.py")
    else:
        print("❌ Fout: dashboard endpoint niet gevonden")
