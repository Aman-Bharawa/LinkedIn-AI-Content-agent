# Chrome side panel

The extension opens a private side panel from its toolbar icon. It does not request LinkedIn page access, read page content, inject scripts, or automate LinkedIn actions.

## Load it in Chrome

1. Open `chrome://extensions`.
2. Turn on **Developer mode**.
3. Select **Load unpacked** and choose this `extension` folder.
4. Pin **LinkedIn AI Content Agent** from Chrome's Extensions menu and select its icon to open the panel.
5. Select **Open secure pairing page**. Sign in to the web app if asked, then copy the one-time code into the side panel.

The current manifest allows the Render app and local Django development URLs. For a different deployment hostname, update `host_permissions`, `APP_ORIGIN` in `sidepanel.js`, and the full workspace link in `sidepanel.html`.

Pairing codes expire after five minutes and work once. The extension receives a revocable 90-day token and stores it in Chrome local extension storage. Disconnect from the panel to revoke that token.
