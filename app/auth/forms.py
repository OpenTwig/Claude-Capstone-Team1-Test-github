from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Length


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(message="Email is required."), Length(max=320)])
    password = PasswordField("Password", validators=[DataRequired(message="Password is required.")])
    stay_signed_in = BooleanField("Stay signed in")
    submit = SubmitField("Submit")


class ChangePasswordForm(FlaskForm):
    """First-login password change (R18.5). The password rules themselves live in services/validation.py."""

    new_password = PasswordField("New password")
    confirm_password = PasswordField("Confirm new password")
    submit = SubmitField("Save password")
