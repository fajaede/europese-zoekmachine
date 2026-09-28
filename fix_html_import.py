with open('backend/main.py', 'r') as f:
    content = f.read()

# Vervang de fastapi import
if 'from fastapi.responses import HTMLResponse' not in content:
    content = content.replace(
        'from fastapi import FastAPI, HTTPException, Request',
        'from fastapi import FastAPI, HTTPException, Request\nfrom fastapi.responses import HTMLResponse'
    )
    
    with open('backend/main.py', 'w') as f:
        f.write(content)
    
    print("✅ HTMLResponse import toegevoegd!")
else:
    print("HTMLResponse al geïmporteerd!")
