from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import admin_only, audit, current_user, staff_or_admin
from ..models import PasswordReset, Role, User
from ..schemas import ForgotIn, LoginIn, RegisterIn, ResetIn, StaffIn, TokenOut, UserOut
from ..security import (create_token, hash_password, hash_reset_token, new_reset_token,
                        validate_password, verify_password)
from ..services.mailer import send_email
from ..config import settings

router = APIRouter(tags=["auth"])


def _create_user(db: Session, data: RegisterIn, role: Role) -> User:
    errs = validate_password(data.password)
    if errs:
        raise HTTPException(422, {"password": errs})
    if db.scalar(select(User).where(User.email == data.email.lower())):
        raise HTTPException(409, "An account with this email already exists")
    user = User(email=data.email.lower(), name=data.name, hashed_password=hash_password(data.password), role=role)
    db.add(user)
    db.flush()
    return user


@router.post("/auth/register", response_model=TokenOut, status_code=201)
def register(data: RegisterIn, db: Session = Depends(get_db)):
    user = _create_user(db, data, Role.customer)   # public signup is ALWAYS a customer
    db.commit()
    return TokenOut(access_token=create_token(user.id, user.role.value), user=user)


@router.post("/auth/login", response_model=TokenOut)
def login(data: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if not user or not verify_password(data.password, user.hashed_password):
        raise HTTPException(401, "Invalid email or password")
    return TokenOut(access_token=create_token(user.id, user.role.value), user=user)


@router.post("/auth/forgot-password")
def forgot_password(data: ForgotIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == data.email.lower()))
    if user:  # same response either way -> no account enumeration
        raw, hashed = new_reset_token()
        db.add(PasswordReset(user_id=user.id, token_hash=hashed, expires_at=datetime.utcnow() + timedelta(minutes=30)))
        db.commit()
        send_email(user.email, "Reset your Brightloop password",
                   f"Use this link within 30 minutes:\n{settings.FRONTEND_URL}/reset-password?token={raw}")
    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/auth/reset-password")
def reset_password(data: ResetIn, db: Session = Depends(get_db)):
    errs = validate_password(data.new_password)
    if errs:
        raise HTTPException(422, {"password": errs})
    pr = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == hash_reset_token(data.token)))
    if not pr or pr.used or pr.expires_at < datetime.utcnow():
        raise HTTPException(400, "Reset link is invalid or has expired")
    db.get(User, pr.user_id).hashed_password = hash_password(data.new_password)
    pr.used = True
    db.commit()
    return {"message": "Password updated"}


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@router.post("/users/staff", response_model=UserOut, status_code=201)
def add_staff(data: StaffIn, db: Session = Depends(get_db), admin: User = Depends(admin_only)):
    user = _create_user(db, data, Role.staff)       # only admin can add staff
    audit(db, admin, "create_staff", "user", user.id)
    db.commit()
    return user


@router.get("/users/customers", response_model=list[UserOut])
def list_customers(db: Session = Depends(get_db), _: User = Depends(staff_or_admin)):
    return db.scalars(select(User).where(User.role == Role.customer).order_by(User.id.desc()).limit(500)).all()
