"""Unit tests for account schemas / password rules (no DB needed)."""

import pytest
from pydantic import ValidationError

from app.schemas.user import ChangePassword, UserUpdate, _validate_password


def test_password_rule_accepts_strong():
    assert _validate_password("Lechuga12") == "Lechuga12"


def test_password_rule_rejects_short():
    with pytest.raises(ValueError):
        _validate_password("ab1")


def test_password_rule_rejects_no_letters():
    with pytest.raises(ValueError):
        _validate_password("12345678")


def test_change_password_schema_validates_new():
    with pytest.raises(ValidationError):
        ChangePassword(current_password="whatever", new_password="short")
    ok = ChangePassword(current_password="old", new_password="Tomate2026")
    assert ok.new_password == "Tomate2026"


def test_user_update_is_partial():
    u = UserUpdate(full_name="Ana")
    assert u.full_name == "Ana"
    assert u.preferences is None and u.notifications is None
