# Finish Google sign-in for Buyntiq

The Supabase `[accounts]` setting stores watchlists and recent searches.
Google sign-in also needs its own `[auth]` settings in Streamlit. Saving a
database URL alone does not enable the Google button. The repository's
`secrets.example.toml` is an example; Streamlit does not load it as real secrets.

## 1. Create Google's login credentials

1. Open [Google Cloud Console](https://console.cloud.google.com/) and select or
   create a project. Open **Google Auth Platform**.
2. In **Branding**, use **Buyntiq** as the app name and complete the required
   contact details.
3. In **Audience**, choose **External** if people outside your organization
   should be able to log in. Add the Google email you will use under **Test users**.
4. In **Clients**, select **Create client**, then **Web application**.
5. Under **Authorized redirect URIs**, add your deployed Streamlit URL followed
   by `/oauth2callback`. For example, ONLY if your app is still at
   `https://buyntiq-v2.streamlit.app`, enter:

   ```text
   https://buyntiq-v2.streamlit.app/oauth2callback
   ```

6. Create the client. Copy its **Client ID** and **Client secret** for the next
   step. These come from Google, not Supabase.

Use the Streamlit app address, not a GitHub address or a Supabase callback.
The redirect URI must match exactly in Google and Streamlit.

## 2. Add Google settings to Streamlit Secrets

Open your deployed app's **Manage app → Settings → Secrets**.
Keep the existing `[accounts]` section and its database URL.
If there is no `[auth]` section yet, append this block BELOW the existing settings:

```toml
[auth]
redirect_uri = "https://YOUR-APP.streamlit.app/oauth2callback"
cookie_secret = "REPLACE_WITH_A_LONG_RANDOM_SECRET"
client_id = "YOUR_GOOGLE_CLIENT_ID.apps.googleusercontent.com"
client_secret = "YOUR_GOOGLE_CLIENT_SECRET"
server_metadata_url = "https://accounts.google.com/.well-known/openid-configuration"
```

Replace all four placeholder values:

| Setting | Value to put between the quotation marks |
| --- | --- |
| `redirect_uri` | The exact callback URL you added in Google |
| `cookie_secret` | A long random value from your password manager; keep it stable |
| `client_id` | Google's complete Client ID, including its existing suffix |
| `client_secret` | Google's Client secret |

Keep `[auth]`, the quotation marks, and the complete `server_metadata_url` line.
Do not add square brackets around the values. Do not append another
`.apps.googleusercontent.com` if the copied ID already includes it.

If `[auth]` already exists, edit its keys instead of creating a second section.
The updated account code also supports Google credentials inside `[auth.google]`;
in that layout, `redirect_uri` and `cookie_secret` still belong directly under
`[auth]`. Use one layout consistently.

For a random cookie secret, you can also run this on your own computer:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Click **Save**. Reboot the app if it does not reload the settings automatically.
Real credentials belong in Streamlit Secrets, never in GitHub or a chat message.

## 3. Check login and saved lists

1. Open **Account → Sign in with Google**. Complete Google's sign-in.
2. Confirm your name appears in Account. Edit and save your watchlist.
3. Sign out, then sign in with the same Google account. The saved list should return.
4. If Account shows a sync warning, use **Retry sync** after checking the database.

While the Google project is in Testing, use an email added to its Test users.
Before opening registration more widely, finish the publishing requirements
shown under Google's **Audience** settings.

## Supabase setup, if not completed earlier

In your Supabase project, open **SQL Editor** and run the repository's
`account_schema.sql`. It creates the private table and preserves existing rows
when rerun. In **Connect**, copy the **Session pooler** PostgreSQL URI. Put it in
Streamlit Secrets as `[accounts]` → `database_url`, replacing the password
placeholder with the database password. Percent-encode reserved password
characters in the URI, for example `@` as `%40`.

This app uses Supabase for storage and Streamlit for Google login. Enabling
Google under Supabase Auth does not configure this app's `st.login()` flow.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Google sign-in setup is incomplete | The five Google settings above exist in Streamlit Secrets, with no empty values or example placeholders. The metadata URL must match exactly. |
| `redirect_uri_mismatch` | The full callback URI matches in Google and Streamlit. |
| Google denies access | Your email is a Test user; also check the project's audience and any organization restrictions. |
| Login works but lists cannot sync | The SQL setup ran and `[accounts].database_url` has the correct pooler address and password. |
| Tab still shows the old square | Confirm `assets/favicon.svg` was added, then refresh or open the app in a new tab. |

These checks detect incomplete local configuration. They cannot prove that a
Google credential is valid or that the hosted database is reachable. Test both
with your deployed app after saving the private settings.

Official references: [Streamlit Google login](https://docs.streamlit.io/develop/tutorials/authentication/google),
[Streamlit login settings](https://docs.streamlit.io/develop/api-reference/user/st.login),
[Streamlit app settings](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/app-settings),
[Supabase database connections](https://supabase.com/docs/guides/database/connecting-to-postgres).
