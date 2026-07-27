"""Initialize database with test user."""

import asyncio
import logging

from sqlalchemy import select

from app.core.security import hash_password
from app.db.base import Base
from app.db.session import AsyncSessionLocal, engine
from app.models.user import User

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def init_db():
    """Create tables and initialize test user."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        # Check if test user exists
        result = await session.execute(
            select(User).where(User.email == "123@gmail.com")
        )
        user = result.scalar_one_or_none()

        if not user:
            # Create test user with password 12345678
            user = User(
                email="123@gmail.com",
                hashed_password=hash_password("12345678"),
                role="farmer",
            )
            session.add(user)
            await session.commit()
            logger.info("✓ Test user created: 123@gmail.com")
        else:
            logger.info("✓ Test user already exists: 123@gmail.com")


if __name__ == "__main__":
    asyncio.run(init_db())
