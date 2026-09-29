"""Field encryption and masking (task 1.3; N1.3, DD8)."""
import pytest
from cryptography.fernet import Fernet
from hypothesis import given
from hypothesis import strategies as st

from app import create_app
from app.crypto import DecryptionError, FieldCipher, decrypt_field, encrypt_field, mask_last4
from tests.conftest import TEST_KEYS

NEW_KEY, OLD_KEY = TEST_KEYS


def test_round_trip():
    cipher = FieldCipher(TEST_KEYS)
    assert cipher.decrypt(cipher.encrypt("123-45-6789")) == "123-45-6789"


@given(st.text(max_size=200))
def test_round_trip_for_any_text(text):
    cipher = FieldCipher(TEST_KEYS)
    assert cipher.decrypt(cipher.encrypt(text)) == text


@pytest.mark.parametrize("secret", ["123-45-6789", "123456789", "000123456789"])
def test_ciphertext_does_not_reveal_the_value_or_its_last_4(secret):  # N1.3, D8
    token = FieldCipher(TEST_KEYS).encrypt(secret)
    digits = secret.replace("-", "")
    assert secret not in token
    assert digits not in token
    assert digits[-4:] not in token


def test_same_value_encrypts_differently_each_time():
    cipher = FieldCipher(TEST_KEYS)
    assert cipher.encrypt("123456789") != cipher.encrypt("123456789")


def test_values_encrypted_with_an_old_key_still_decrypt_after_rotation():
    old_token = FieldCipher([OLD_KEY]).encrypt("987654321")
    rotated_cipher = FieldCipher([NEW_KEY, OLD_KEY])  # new key added in front
    assert rotated_cipher.decrypt(old_token) == "987654321"


def test_new_values_use_the_newest_key():
    token = FieldCipher([NEW_KEY, OLD_KEY]).encrypt("987654321")
    assert FieldCipher([NEW_KEY]).decrypt(token) == "987654321"
    with pytest.raises(DecryptionError):
        FieldCipher([OLD_KEY]).decrypt(token)


def test_rotate_re_encrypts_with_the_newest_key():
    old_token = FieldCipher([OLD_KEY]).encrypt("555")
    rotated = FieldCipher([NEW_KEY, OLD_KEY]).rotate(old_token)
    assert FieldCipher([NEW_KEY]).decrypt(rotated) == "555"


def test_wrong_key_raises_decryption_error():
    token = FieldCipher([NEW_KEY]).encrypt("123456789")
    with pytest.raises(DecryptionError):
        FieldCipher([Fernet.generate_key().decode()]).decrypt(token)


@pytest.mark.parametrize("garbage", ["", "not-a-token", "gAAAAABtampered", "ünicode"])
def test_garbage_raises_decryption_error(garbage):
    with pytest.raises(DecryptionError):
        FieldCipher(TEST_KEYS).decrypt(garbage)


def test_cipher_requires_a_key():
    with pytest.raises(ValueError):
        FieldCipher([])


def test_app_level_helpers_use_the_configured_keys(valid_env):
    app = create_app(valid_env)
    with app.app_context():
        token = encrypt_field("123-45-6789")
        assert decrypt_field(token) == "123-45-6789"
    assert FieldCipher(TEST_KEYS).decrypt(token) == "123-45-6789"


@pytest.mark.parametrize(
    "value, masked",
    [
        ("123456789", "•••• 6789"),
        ("000123456789", "•••• 6789"),
        ("12345", "•••• 2345"),
        ("1234", "••••"),       # too short to show anything safely
        ("12", "••••"),
        ("", "••••"),
        (None, "••••"),
        ("  987654321  ", "•••• 4321"),
    ],
)
def test_mask_last4(value, masked):  # R10.5
    assert mask_last4(value) == masked
