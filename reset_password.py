
from getpass import getpass
from werkzeug.security import generate_password_hash
from app import app, mysql

old_email = input("Current registered email: ").strip().lower()
new_email = input("New email address: ").strip().lower()

new_password = getpass("New password: ")
confirm_password = getpass("Confirm new password: ")

if not new_email or "@" not in new_email:
    print("Please enter a valid email address.")

elif len(new_password) < 8:
    print("Password must be at least 8 characters.")

elif new_password != confirm_password:
    print("Passwords do not match. Nothing was changed.")

else:
    with app.app_context():
        relationship = mysql.connection.cursor()

        relationship.execute(
            "SELECT user_id FROM users WHERE email=%s",
            (old_email,)
        )
        user = relationship.fetchone()

        if not user:
            print("Current email was not found. Nothing was changed.")

        else:
            relationship.execute(
                "SELECT user_id FROM users WHERE email=%s AND user_id<>%s",
                (new_email, user["user_id"])
            )

            if relationship.fetchone():
                print("That new email is already registered.")

            else:
                relationship.execute(
                    "UPDATE users SET email=%s, password=%s WHERE user_id=%s",
                    (
                        new_email,
                        generate_password_hash(new_password),
                        user["user_id"]
                    )
                )

                mysql.connection.commit()
                print("Email and password updated successfully!")

        relationship.close()