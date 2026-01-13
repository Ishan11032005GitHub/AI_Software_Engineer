from app.database import db
from app.auth.security import hash_password, verify_password, create_access_token

def register_user(email: str, password: str):
    if db.users.find_one({"email": email}):
        raise ValueError("User already exists")

    db.users.insert_one({
        "email": email,
        "password": hash_password(password)
    })

def login_user(email: str, password: str):
    user = db.users.find_one({"email": email})
    if not user or not verify_password(password, user["password"]):
        raise ValueError("Invalid credentials")

    return create_access_token({"sub": email})
