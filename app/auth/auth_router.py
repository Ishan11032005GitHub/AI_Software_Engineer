from fastapi import APIRouter, HTTPException
from app.auth.auth_service import register_user, login_user

router = APIRouter(prefix="/auth")

@router.post("/register")
def register(email: str, password: str):
    try:
        register_user(email, password)
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(400, str(e))

@router.post("/login")
def login(email: str, password: str):
    try:
        return {"token": login_user(email, password)}
    except ValueError as e:
        raise HTTPException(401, str(e))
