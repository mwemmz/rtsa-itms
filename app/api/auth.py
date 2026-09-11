from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.ratelimit import check_rate_limit, record_failure, reset
from app.core.security import create_access_token, get_current_user, hash_password, require_role, verify_password
from app.models.user import User, UserRole
from app.schemas.user import LoginRequest, Token, UserCreate, UserResponse
from app.services.audit import log_action

router = APIRouter(prefix="/api/auth", tags=["Auth"])

_FORM_SCHEMA = {
    "type": "object",
    "required": ["username", "password"],
    "properties": {
        "grant_type": {"type": "string", "enum": ["password"]},
        "username": {"type": "string", "description": "Account email address"},
        "password": {"type": "string", "format": "password"},
        "scope": {"type": "string", "default": ""},
        "client_id": {"type": "string"},
        "client_secret": {"type": "string"},
    },
}


async def _credentials(request: Request) -> tuple[str | None, str | None]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            return None, None
        email = body.get("email")
        password = body.get("password")
    else:
        form = await request.form()
        email = form.get("email") or form.get("username")
        password = form.get("password")
    return (email if isinstance(email, str) else None), (
        password if isinstance(password, str) else None
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=payload.role,
    )
    db.add(user)
    db.flush()
    log_action(db, "register", "user", str(user.id), f"Registered as {payload.role.value}", user.id)
    db.commit()
    db.refresh(user)
    return user


@router.post(
    "/login",
    response_model=Token,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": LoginRequest.model_json_schema()},
                "application/x-www-form-urlencoded": {"schema": _FORM_SCHEMA},
            },
        }
    },
)
async def login(request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    rate_key = f"login:{client_ip}"
    if not check_rate_limit(rate_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
        )

    email, password = await _credentials(request)
    if not email or not password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide email and password as JSON body or OAuth2 form data",
        )

    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        record_failure(rate_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    reset(rate_key)
    token = create_access_token(data={"sub": str(user.id), "role": user.role.value})
    return Token(access_token=token)


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/users", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    return db.query(User).all()
