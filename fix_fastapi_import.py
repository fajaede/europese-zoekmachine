with open('backend/main.py', 'r') as f:
    content = f.read()

# Voeg HTMLResponse toe aan de bestaande fastapi import
if 'HTMLResponse' not in content:
    # Zoek de fastapi import en voeg HTMLResponse toe
    content = content.replace(
        '''from fastapi import (
    Depends,
    FastAPI,
    Request,
    HTTPException,
    BackgroundTasks,''',
        '''from fastapi import (
    Depends,
    FastAPI,
    Request,
    HTTPException,
    BackgroundTasks,
    HTMLResponse,'''
    )
    
    with open('backend/main.py', 'w') as f:
        f.write(content)
    
    print("✅ HTMLResponse toegevoegd aan fastapi import!")
else:
    print("HTMLResponse al aanwezig!")
