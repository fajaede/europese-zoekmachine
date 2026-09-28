"""Voeg developer router toe aan main.py"""

# Lees main.py
with open('backend/main.py', 'r') as f:
    content = f.read()

# Check of het al bestaat
if 'from api.developer_api import' in content:
    print("Developer router al toegevoegd!")
else:
    # Voeg import toe na andere imports
    import_line = "from api.developer_api import router as developer_router\n"
    
    # Zoek een goede plek voor de import (na andere api imports)
    if 'from api.' in content:
        # Voeg toe na bestaande api import
        lines = content.split('\n')
        new_lines = []
        for i, line in enumerate(lines):
            new_lines.append(line)
            if line.startswith('from api.') and 'developer' not in line:
                if import_line not in content:
                    new_lines.append(import_line)
        content = '\n'.join(new_lines)
    else:
        # Voeg toe na de laatste import
        content = import_line + content
    
    # Voeg router include toe na monitoring_router
    router_line = '\napp.include_router(developer_router, prefix="/api/developers", tags=["Developers"])\n'
    content = content.replace(
        'app.include_router(monitoring_router, prefix="/monitoring", tags=["Monitoring"])',
        'app.include_router(monitoring_router, prefix="/monitoring", tags=["Monitoring"])\napp.include_router(developer_router, prefix="/api/developers", tags=["Developers"])'
    )
    
    # Schrijf terug
    with open('backend/main.py', 'w') as f:
        f.write(content)
    
    print("✅ Developer router toegevoegd aan main.py!")

# Verifieer
with open('backend/main.py', 'r') as f:
    if 'developer_router' in f.read():
        print("✅ Verifieerd: developer_router staat in main.py")
    else:
        print("❌ Fout: developer_router niet gevonden")
