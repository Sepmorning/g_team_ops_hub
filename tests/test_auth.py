import sqlite3

import pytest

from anda_tracker.auth import UserRepository, verify_password
from anda_tracker.errors import ConfigurationError
from anda_tracker.storage import ProjectDatabase


def test_bootstrap_admin_hashes_login_password_and_migrates_default_profile(tmp_path):
    path = tmp_path / "app.db"
    legacy = ProjectDatabase(path)
    legacy.save_credentials("anda", "carrier-user", "carrier-secret")

    users = UserRepository(path)
    assert users.migration_backup_path is not None
    assert users.migration_backup_path.exists()
    admin = users.create_user(
        "admin",
        "系统管理员",
        "StrongPass123",
        role="admin",
        migrate_default_profile=True,
    )

    with sqlite3.connect(path) as connection:
        password_hash = connection.execute(
            "SELECT password_hash FROM users WHERE id=?", (admin.id,)
        ).fetchone()[0]
        profile_id = connection.execute(
            "SELECT profile_id FROM carrier_credentials"
        ).fetchone()[0]
    assert "StrongPass123" not in password_hash
    assert verify_password("StrongPass123", password_hash)
    assert profile_id == admin.id
    assert ProjectDatabase(path, profile_id=admin.id).load_credentials("anda").username == "carrier-user"


def test_initial_admin_creation_is_rejected_after_system_is_initialized(tmp_path):
    users = UserRepository(tmp_path / "app.db")
    users.create_user(
        "admin",
        "管理员",
        "AdminPass123",
        role="admin",
        only_if_empty=True,
    )
    with pytest.raises(ConfigurationError, match="已经完成初始化"):
        users.create_user(
            "second-admin",
            "第二管理员",
            "AdminPass456",
            role="admin",
            only_if_empty=True,
        )


def test_login_is_case_insensitive_and_inactive_user_cannot_login(tmp_path):
    users = UserRepository(tmp_path / "app.db")
    admin = users.create_user("Admin", "管理员", "StrongPass123", role="admin")
    member = users.create_user("Member", "成员", "MemberPass123")
    assert users.authenticate("member", "MemberPass123").id == member.id
    users.set_active(admin.id, member.id, False)
    with pytest.raises(ConfigurationError, match="已停用"):
        users.authenticate("MEMBER", "MemberPass123")


def test_non_admin_cannot_manage_accounts(tmp_path):
    users = UserRepository(tmp_path / "app.db")
    member = users.create_user("member", "成员", "MemberPass123")
    other = users.create_user("other", "其他", "OtherPass123")
    with pytest.raises(ConfigurationError, match="管理员"):
        users.set_active(member.id, other.id, False)
    with pytest.raises(ConfigurationError, match="管理员"):
        users.reset_password(member.id, other.id, "NewPass123")


def test_admin_reset_requires_user_to_change_password(tmp_path):
    users = UserRepository(tmp_path / "app.db")
    admin = users.create_user("admin", "管理员", "AdminPass123", role="admin")
    member = users.create_user("member", "成员", "MemberPass123")
    users.reset_password(admin.id, member.id, "Temporary123")
    logged_in = users.authenticate("member", "Temporary123")
    assert logged_in.must_change_password
    users.change_password(member.id, "Temporary123", "FinalPass123")
    assert not users.authenticate("member", "FinalPass123").must_change_password


def test_business_secrets_are_isolated_by_user_profile(tmp_path):
    path = tmp_path / "app.db"
    users = UserRepository(path)
    first = users.create_user("first", "甲", "FirstPass123")
    second = users.create_user("second", "乙", "SecondPass123")
    first_db = ProjectDatabase(path, profile_id=first.id)
    second_db = ProjectDatabase(path, profile_id=second.id)
    first_db.save_credentials("anda", "first-carrier", "first-secret")
    second_db.save_credentials("anda", "second-carrier", "second-secret")
    assert first_db.load_credentials("anda").username == "first-carrier"
    assert second_db.load_credentials("anda").username == "second-carrier"


def test_admin_cannot_disable_current_account(tmp_path):
    users = UserRepository(tmp_path / "app.db")
    admin = users.create_user("admin", "管理员", "AdminPass123", role="admin")
    with pytest.raises(ConfigurationError, match="不能停用"):
        users.set_active(admin.id, admin.id, False)
