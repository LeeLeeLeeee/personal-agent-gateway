from personal_agent_gateway.auth_store import AuthStore


def test_totp_setup_generates_secret_and_otpauth_uri(tmp_path):
    store = AuthStore(tmp_path / "auth")

    setup = store.start_totp_setup(account_name="local-owner")

    assert setup.secret
    assert setup.otpauth_uri.startswith("otpauth://totp/")


def test_totp_verify_enables_login(tmp_path):
    store = AuthStore(tmp_path / "auth")
    setup = store.start_totp_setup(account_name="local-owner")
    code = store.current_code_for_test(setup.secret)

    result = store.verify_totp_setup(code)

    assert result.enabled is True
    assert len(result.recovery_codes) == 10
    assert store.verify_login_code(code) is True


def test_recovery_code_is_single_use(tmp_path):
    store = AuthStore(tmp_path / "auth")
    setup = store.start_totp_setup(account_name="local-owner")
    result = store.verify_totp_setup(store.current_code_for_test(setup.secret))
    recovery_code = result.recovery_codes[0]

    assert store.use_recovery_code(recovery_code) is True
    assert store.use_recovery_code(recovery_code) is False
