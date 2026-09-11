import os
from typing import Optional

from .database import Database
from .repositories.account_repository import AccountRepository
from .repositories.persistence_repository import SecretBox, TelegramSessionRepository


class SqlTelegramSessionStore:
    def __init__(self, db: Database, box: SecretBox):
        self._accounts = AccountRepository(db)
        self._sessions = TelegramSessionRepository(db, box)

    def _account_id(self, phone: str) -> str:
        account = self._accounts.get_by_phone(phone)
        if account is None:
            raise ValueError("Telegram account is not registered in SQL.")
        return account["id"]

    def has(self, phone: str) -> bool:
        try:
            return self._sessions.load(self._account_id(phone)) is not None
        except ValueError:
            return False

    def load(self, phone: str) -> Optional[str]:
        return self._sessions.load(self._account_id(phone))

    def save(self, phone: str, session_string: str) -> None:
        self._sessions.save(self._account_id(phone), session_string)

    def delete(self, phone: str) -> None:
        self._sessions.delete(self._account_id(phone))
