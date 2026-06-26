import os
import json
import warnings

import requests  # type: ignore[import]
import streamlit as st

from pages.api_client import ApiClient, ApiError, detect_backend

warnings.filterwarnings("ignore")


st.set_page_config(
    page_title="RAPAS Photometry Pipeline - Login",
    page_icon=":sparkles:",
    layout="centered",
)

# Use session state to track login status - Keep these basic initializations
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = None
if "recovery_step" not in st.session_state:  # Keep for password recovery logic
    st.session_state.recovery_step = 0
if "api_mode" not in st.session_state:
    st.session_state.api_mode = False
if "api_base_url" not in st.session_state:
    st.session_state.api_base_url = os.getenv(
        "RPP_API_URL",
        "http://localhost:8000",
    )
if "legacy_backend_url" not in st.session_state:
    st.session_state.legacy_backend_url = os.getenv(
        "RPP_LEGACY_URL",
        "http://localhost:5000",
    )
if "backend_mode" not in st.session_state:
    st.session_state.backend_mode = "legacy"
if "api_credentials" not in st.session_state:
    st.session_state.api_credentials = None
if "backend_status_message" not in st.session_state:
    st.session_state.backend_status_message = "Determining backend..."
if "backend_initialized" not in st.session_state:
    st.session_state.backend_initialized = False

if not st.session_state.backend_initialized:
    backend_info = detect_backend()
    st.session_state.api_base_url = backend_info["api_base_url"]
    st.session_state.legacy_backend_url = backend_info["legacy_backend_url"]
    st.session_state.api_mode = backend_info["mode"] == "api"
    st.session_state.backend_mode = backend_info["mode"]
    st.session_state.backend_status_message = backend_info["message"]
    st.session_state.backend_initialized = True


def validate_password(password):
    """Validate password meets all requirements"""
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter."
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one digit."
    return True, ""


if not st.session_state.logged_in:
    st.title(":sparkles: RAPAS Photometry Pipeline")
    st.caption(st.session_state.backend_status_message)

    with st.expander("👋 First time ? / Première fois ?"):
        guide_en, guide_fr = st.tabs(["🇬🇧 English", "🇫🇷 Français"])
        with guide_en:
            st.markdown(
                """
**Welcome to RAPAS — the RAPAS Photometry Pipeline!**

Here is what you need to get started:

1. **Create an account** — go to the *Sign Up* tab below and register with a username, password, and email.
2. **Login** — use your credentials in the *Login* tab.
3. **Upload a FITS image** — images must be **16-bit or 32-bit** (8-bit images are not supported).
4. **Configure your observatory** — fill in your site coordinates in the *Observatory Data* panel.
5. **Set analysis parameters** — adjust detection and photometry settings in the *Analysis Parameters* panel.
6. **Run the pipeline** — click **▶️ Start Analysis** and download your results as a ZIP archive.

Need help or want to report a bug? Contact [rpp_support](mailto:rpp_support@saf-astronomie.fr).
"""
            )
        with guide_fr:
            st.markdown(
                """
**Bienvenue dans RAPAS — le pipeline de photométrie RAPAS !**

Voici ce dont vous avez besoin pour commencer :

1. **Créer un compte** — rendez-vous dans l'onglet *Sign Up* ci-dessous et inscrivez-vous avec un nom d'utilisateur, un mot de passe et une adresse e-mail.
2. **Se connecter** — utilisez vos identifiants dans l'onglet *Login*.
3. **Charger une image FITS** — les images doivent être encodées sur **16 bits ou 32 bits** (les images 8 bits ne sont pas prises en charge).
4. **Configurer votre observatoire** — renseignez les coordonnées de votre site dans le panneau *Observatory Data*.
5. **Régler les paramètres d'analyse** — ajustez les paramètres de détection et de photométrie dans le panneau *Analysis Parameters*.
6. **Lancer le pipeline** — cliquez sur **▶️ Start Analysis** et téléchargez vos résultats sous forme d'archive ZIP.

Besoin d'aide ou vous avez trouvé un bug ? Contactez [rpp_support](mailto:rpp_support@saf-astronomie.fr).
"""
            )

    tab_login, tab_signup, tab_recovery = st.tabs(
        ["🔑 Login", "📝 Sign Up", "🔓 Password Recovery"]
    )

    with tab_login:
        st.subheader("Login to your account")
        login_username = st.text_input(
            "Username",
            key="login_username",
            placeholder="Enter your username",
        )
        login_password = st.text_input(
            "Password",
            key="login_password",
            type="password",
            placeholder="Enter your password",
        )
        if st.button("Login", key="login_btn", use_container_width=True):
            if st.session_state.backend_mode == "unavailable":
                st.error(st.session_state.backend_status_message)
            elif login_username and login_password:
                if st.session_state.api_mode:
                    client = ApiClient(st.session_state.api_base_url)
                    try:
                        message = client.login(
                            username=login_username,
                            password=login_password,
                        )
                    except ApiError as exc:
                        st.error(f"API error: {exc.message}")
                    else:
                        st.session_state.logged_in = True
                        st.session_state.username = login_username
                        st.session_state.backend_mode = "api"
                        st.session_state.api_credentials = {
                            "username": login_username,
                            "password": login_password,
                            "base_url": st.session_state.api_base_url,
                        }
                        st.rerun()
                else:
                    legacy_url = st.session_state.legacy_backend_url
                    try:
                        response = requests.post(
                            f"{legacy_url}/login",
                            data={"username": login_username, "password": login_password},
                        )
                        if response.status_code == 200:
                            st.session_state.logged_in = True
                            st.session_state.username = login_username
                            st.session_state.backend_mode = "legacy"
                            st.rerun()
                        else:
                            st.error(response.text)
                    except requests.exceptions.RequestException as e:
                        st.error(
                            f"Connection error: Could not reach the backend server. {e}"
                        )
            else:
                st.warning("Please enter both username and password.")

    with tab_signup:
        st.subheader("Create a new account")
        st.info(
            "Password requirements: at least 8 characters, one uppercase letter, "
            "one lowercase letter, and one digit."
        )
        reg_username = st.text_input(
            "Username",
            key="reg_username",
            placeholder="Choose a username",
        )
        reg_password = st.text_input(
            "Password",
            key="reg_password",
            type="password",
            placeholder="Choose a password",
        )
        reg_email = st.text_input(
            "Email",
            key="reg_email",
            placeholder="your@email.com",
            help="Used for password recovery only.",
        )
        if st.button("Sign Up", key="signup_btn", use_container_width=True):
            if st.session_state.backend_mode == "unavailable":
                st.error(st.session_state.backend_status_message)
            elif reg_username and reg_password and reg_email:
                is_valid, error_message = validate_password(reg_password)
                if not is_valid:
                    st.warning(error_message)
                else:
                    if st.session_state.api_mode:
                        client = ApiClient(st.session_state.api_base_url)
                        try:
                            message = client.register(
                                username=reg_username,
                                password=reg_password,
                                email=reg_email,
                            )
                        except ApiError as exc:
                            st.error(f"API error: {exc.message}")
                        else:
                            st.success(message)
                    else:
                        legacy_url = st.session_state.legacy_backend_url
                        try:
                            response = requests.post(
                                f"{legacy_url}/register",
                                data={
                                    "username": reg_username,
                                    "password": reg_password,
                                    "email": reg_email,
                                },
                            )
                            if response.status_code == 201:
                                st.success(response.text)
                            else:
                                st.error(response.text)
                        except requests.exceptions.RequestException as e:
                            st.error(
                                f"Connection error: Could not reach the backend server. {e}"
                            )
            else:
                st.warning("Please enter username, password, and email.")

    # ── Password Recovery tab ───────────────────────────────────────────────────────
    with tab_recovery:
        st.subheader("Reset your password")

        # Initialize recovery form fields in session state for persistence
        if "recovery_email_value" not in st.session_state:
            st.session_state.recovery_email_value = ""
        if "recovery_code_value" not in st.session_state:
            st.session_state.recovery_code_value = ""
        if "recovery_new_pw_value" not in st.session_state:
            st.session_state.recovery_new_pw_value = ""

        recovery_email = st.text_input(
            "Registered email address",
            value=st.session_state.recovery_email_value,
            key="recovery_email",
            placeholder="your@email.com",
            on_change=lambda: st.session_state.update(
                {"recovery_email_value": st.session_state.recovery_email}
            ),
        )

        # Step 0: request recovery code
        if st.session_state.recovery_step == 0:
            if st.button(
                "Send Recovery Code", key="send_recovery_btn", use_container_width=True
            ):
                if st.session_state.backend_mode == "unavailable":
                    st.error(st.session_state.backend_status_message)
                elif recovery_email:
                    if st.session_state.backend_mode == "api":
                        client = ApiClient(st.session_state.api_base_url)
                        try:
                            message = client.request_password_recovery(recovery_email)
                        except ApiError as exc:
                            st.error(f"API error: {exc.message}")
                        else:
                            st.session_state.recovery_step = 1
                            st.success(message)
                            st.rerun()
                    else:
                        try:
                            resp = requests.post(
                                f"{st.session_state.legacy_backend_url}/recover_request",
                                data={"email": recovery_email},
                            )
                            if resp.status_code == 200:
                                st.session_state.recovery_step = 1
                                st.success("Recovery code sent to your email.")
                                st.rerun()
                            else:
                                st.error(resp.text)
                        except requests.exceptions.RequestException as e:
                            st.error(f"Connection error: Backend unreachable. {e}")
                else:
                    st.warning("Please enter your email.")

        # Step 1: enter code and new password
        elif st.session_state.recovery_step == 1:
            st.info(
                "A recovery code has been sent to your email. "
                "Enter it below together with your new password."
            )
            code = st.text_input(
                "Recovery Code",
                value=st.session_state.recovery_code_value,
                key="recovery_code",
                placeholder="6-digit code",
                on_change=lambda: st.session_state.update(
                    {"recovery_code_value": st.session_state.recovery_code}
                ),
            )
            new_password = st.text_input(
                "New Password",
                type="password",
                value=st.session_state.recovery_new_pw_value,
                key="recovery_new_pw",
                on_change=lambda: st.session_state.update(
                    {"recovery_new_pw_value": st.session_state.recovery_new_pw}
                ),
            )
            col_reset, col_cancel = st.columns([2, 1])
            with col_reset:
                if st.button(
                    "Reset Password", key="reset_pw_btn", use_container_width=True
                ):
                    if st.session_state.backend_mode == "unavailable":
                        st.error(st.session_state.backend_status_message)
                    elif recovery_email and code and new_password:
                        is_valid, error_message = validate_password(new_password)
                        if not is_valid:
                            st.warning(error_message)
                        else:
                            if st.session_state.backend_mode == "api":
                                client = ApiClient(st.session_state.api_base_url)
                                try:
                                    message = client.confirm_password_recovery(
                                        email=recovery_email,
                                        code=code,
                                        new_password=new_password,
                                    )
                                except ApiError as exc:
                                    st.error(f"API error: {exc.message}")
                                else:
                                    st.success(message)
                                    st.session_state.recovery_step = 0
                                    st.session_state.recovery_email_value = ""
                                    st.session_state.recovery_code_value = ""
                                    st.session_state.recovery_new_pw_value = ""
                                    st.rerun()
                            else:
                                try:
                                    resp = requests.post(
                                        f"{st.session_state.legacy_backend_url}/recover_confirm",
                                        data={
                                            "email": recovery_email,
                                            "code": code,
                                            "new_password": new_password,
                                        },
                                    )
                                    if resp.status_code == 200:
                                        st.success("Password updated. You can now log in.")
                                        st.session_state.recovery_step = 0
                                        st.session_state.recovery_email_value = ""
                                        st.session_state.recovery_code_value = ""
                                        st.session_state.recovery_new_pw_value = ""
                                        st.rerun()
                                    else:
                                        st.error(resp.text)
                                except requests.exceptions.RequestException as e:
                                    st.error(f"Connection error: Backend unreachable. {e}")
                    else:
                        st.warning("Please enter all fields.")
            with col_cancel:
                if st.button(
                    "Cancel", key="cancel_recovery_btn", use_container_width=True
                ):
                    st.session_state.recovery_step = 0
                    st.session_state.recovery_email_value = ""
                    st.session_state.recovery_code_value = ""
                    st.session_state.recovery_new_pw_value = ""
                    st.rerun()

    
else:
    st.success(f"Welcome, {st.session_state.username}! Redirecting to the app...")
    st.sidebar.caption(st.session_state.backend_status_message)
    # Load config from backend and store the raw dictionaries in session state
    try:
        if st.session_state.backend_mode == "api":
            creds = st.session_state.get("api_credentials")
            if creds:
                client = ApiClient(creds.get("base_url"))
                try:
                    config = client.get_config(
                        username=creds["username"],
                        password=creds["password"],
                    )
                except ApiError as exc:
                    st.warning(f"Could not load user config: {exc.message}")
                    config = None
            else:
                config = None
        else:
            config_url = f"{st.session_state.legacy_backend_url}/get_config"
            resp = requests.get(
                config_url,
                params={"username": st.session_state.username},
            )
            if resp.status_code == 200 and resp.text and resp.text != "{}":
                config = json.loads(resp.text)
            else:
                config = None

        if config:
            if "analysis_parameters" in config:
                st.session_state["analysis_parameters"] = config["analysis_parameters"]

            if "observatory_data" in config:
                st.session_state["observatory_data"] = config["observatory_data"]

            if "colibri_api_key" in config:
                st.session_state["colibri_api_key"] = config["colibri_api_key"]
        else:
            st.warning("No stored user configuration found. Using defaults.")

            if "analysis_parameters" not in st.session_state:
                st.session_state["analysis_parameters"] = {}
            if "observatory_data" not in st.session_state:
                st.session_state["observatory_data"] = {}

    except requests.exceptions.RequestException as e:
        st.warning(f"Could not load user config: Connection error - {e}")

        if "analysis_parameters" not in st.session_state:
            st.session_state["analysis_parameters"] = {}
        if "observatory_data" not in st.session_state:
            st.session_state["observatory_data"] = {}
    except Exception as e:
        st.warning(f"Could not load user config: {e}")

        if "analysis_parameters" not in st.session_state:
            st.session_state["analysis_parameters"] = {}
        if "observatory_data" not in st.session_state:
            st.session_state["observatory_data"] = {}

    try:
        st.switch_page("pages/app.py")
    except Exception:
        st.warning(
            "Automatic redirect is not available in this launch mode. "
            "Open the app page directly or start the app with frontend.py."
        )
    st.stop()
