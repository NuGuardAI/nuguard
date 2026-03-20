"""Unit tests for granular PRIVILEGE adapter detection.

One test class per privilege scope, plus:
- TestRegistry  — all 8 adapters registered in default_registry()
- TestNegatives — no false positives on innocent code
- TestMetadata  — privilege_scope metadata field propagated correctly
"""

from __future__ import annotations

import pytest

from xelo.adapters.privilege import privilege_adapters
from xelo.adapters.registry import default_registry
from xelo.types import ComponentType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ADAPTERS_BY_CANON = {a.canonical_name: a for a in privilege_adapters()}

_ALL_CANON = {
    "privilege:rbac",
    "privilege:admin",
    "privilege:filesystem_write",
    "privilege:db_write",
    "privilege:email_out",
    "privilege:social_media_out",
    "privilege:code_execution",
    "privilege:network_out",
}


def _detect(canon: str, code: str):
    """Return the AdapterDetection result (or None) for `canon` applied to `code`."""
    return _ADAPTERS_BY_CANON[canon].detect(code)


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_all_privilege_adapters_registered(self) -> None:
        reg = default_registry()
        reg_canons = {
            a.canonical_name
            for a in reg
            if hasattr(a, "component_type") and a.component_type == ComponentType.PRIVILEGE
        }
        assert _ALL_CANON <= reg_canons, (
            f"Missing privilege adapters in registry: {_ALL_CANON - reg_canons}"
        )

    def test_no_privilege_generic_in_registry(self) -> None:
        reg = default_registry()
        canon_names = {
            a.canonical_name
            for a in reg
            if hasattr(a, "component_type") and a.component_type == ComponentType.PRIVILEGE
        }
        assert "privilege:generic" not in canon_names, (
            "Old generic privilege adapter should be removed from registry"
        )

    def test_all_have_correct_component_type(self) -> None:
        for adapter in privilege_adapters():
            assert adapter.component_type == ComponentType.PRIVILEGE, (
                f"{adapter.name} has wrong component_type: {adapter.component_type}"
            )

    def test_all_have_privilege_scope_metadata(self) -> None:
        for adapter in privilege_adapters():
            scope = adapter.metadata.get("privilege_scope")
            assert scope, f"{adapter.name} missing metadata['privilege_scope']"
            expected = adapter.canonical_name.split(":")[1]
            assert scope == expected, (
                f"{adapter.name}: privilege_scope={scope!r} but canonical={adapter.canonical_name!r}"
            )


# ---------------------------------------------------------------------------
# privilege:rbac
# ---------------------------------------------------------------------------


class TestRBAC:
    def test_has_permission(self) -> None:
        det = _detect("privilege:rbac", "if not user.has_permission('write'): raise Forbidden")
        assert det is not None

    def test_require_permission(self) -> None:
        det = _detect("privilege:rbac", "require_permission(user, 'admin')")
        assert det is not None

    def test_assign_role(self) -> None:
        det = _detect("privilege:rbac", "assign_role(user_id, role='editor')")
        assert det is not None

    def test_rbac_keyword(self) -> None:
        det = _detect("privilege:rbac", "# This service uses RBAC for access control")
        assert det is not None

    def test_least_privilege(self) -> None:
        det = _detect("privilege:rbac", "# Apply least_privilege principle here")
        assert det is not None

    def test_access_control(self) -> None:
        det = _detect("privilege:rbac", "class AccessControl: ...")
        assert det is not None

    def test_decorator_form(self) -> None:
        det = _detect("privilege:rbac", "@require_roles('admin')\ndef create_user(): ...")
        assert det is not None

    def test_multi_match(self) -> None:
        code = "check_permission(u, 'r')\nassign_role(u, 'editor')"
        det = _detect("privilege:rbac", code)
        assert det is not None
        assert len(det.matches) >= 2


# ---------------------------------------------------------------------------
# privilege:admin
# ---------------------------------------------------------------------------


class TestAdmin:
    def test_is_superuser(self) -> None:
        det = _detect("privilege:admin", "if not request.user.is_superuser: raise PermissionDenied")
        assert det is not None

    def test_sudo(self) -> None:
        det = _detect("privilege:admin", "os.system('sudo systemctl restart app')")
        assert det is not None

    def test_is_admin(self) -> None:
        det = _detect("privilege:admin", "if user.is_admin: grant_access()")
        assert det is not None

    def test_setuid(self) -> None:
        det = _detect("privilege:admin", "os.setuid(0)  # run as root")
        assert det is not None

    def test_admin_required_decorator(self) -> None:
        det = _detect("privilege:admin", "@admin_required\ndef admin_panel(): ...")
        assert det is not None

    def test_canonical_name(self) -> None:
        det = _detect("privilege:admin", "is_superuser = True")
        assert det is not None
        assert det.canonical_name == "privilege:admin"


# ---------------------------------------------------------------------------
# privilege:filesystem_write
# ---------------------------------------------------------------------------


class TestFilesystemWrite:
    def test_open_write_mode(self) -> None:
        det = _detect(
            "privilege:filesystem_write", 'with open("report.txt", "w") as f: f.write(data)'
        )
        assert det is not None

    def test_open_append_mode(self) -> None:
        det = _detect("privilege:filesystem_write", 'f = open("log.txt", "a")')
        assert det is not None

    def test_open_binary_write(self) -> None:
        det = _detect("privilege:filesystem_write", 'open("img.png", "wb")')
        assert det is not None

    def test_os_remove(self) -> None:
        det = _detect("privilege:filesystem_write", "os.remove(old_path)")
        assert det is not None

    def test_os_unlink(self) -> None:
        det = _detect("privilege:filesystem_write", "os.unlink(tmp_file)")
        assert det is not None

    def test_shutil_move(self) -> None:
        det = _detect("privilege:filesystem_write", "shutil.move(src, dst)")
        assert det is not None

    def test_write_text(self) -> None:
        det = _detect("privilege:filesystem_write", "Path('out.json').write_text(json.dumps(data))")
        assert det is not None

    def test_file_write_tool(self) -> None:
        det = _detect("privilege:filesystem_write", "tools = [FileWriteTool(), SearchTool()]")
        assert det is not None

    def test_os_makedirs(self) -> None:
        det = _detect("privilege:filesystem_write", "os.makedirs(output_dir, exist_ok=True)")
        assert det is not None

    def test_wb_save(self) -> None:
        det = _detect("privilege:filesystem_write", "wb.save(filepath)")
        assert det is not None

    def test_workbook_save(self) -> None:
        det = _detect("privilege:filesystem_write", "workbook.save(str(path))")
        assert det is not None

    def test_df_to_excel(self) -> None:
        det = _detect("privilege:filesystem_write", "df.to_excel('report.xlsx', index=False)")
        assert det is not None

    def test_writer_save(self) -> None:
        det = _detect("privilege:filesystem_write", "writer.save()")
        assert det is not None

    def test_open_read_mode_no_match(self) -> None:
        """Read-only open should NOT trigger the adapter."""
        det = _detect(
            "privilege:filesystem_write", 'with open("data.txt", "r") as f: content = f.read()'
        )
        assert det is None

    def test_canonical_name(self) -> None:
        det = _detect("privilege:filesystem_write", 'open("x", "w")')
        assert det is not None
        assert det.canonical_name == "privilege:filesystem_write"


# ---------------------------------------------------------------------------
# privilege:db_write
# ---------------------------------------------------------------------------


class TestDbWrite:
    def test_insert_into(self) -> None:
        det = _detect(
            "privilege:db_write", 'cur.execute("INSERT INTO events VALUES (?, ?)", (name, ts))'
        )
        assert det is not None

    def test_update_set(self) -> None:
        det = _detect("privilege:db_write", 'conn.execute("UPDATE users SET active=1 WHERE id=?")')
        assert det is not None

    def test_delete_from(self) -> None:
        det = _detect("privilege:db_write", 'db.execute("DELETE FROM sessions WHERE expired=1")')
        assert det is not None

    def test_session_add(self) -> None:
        det = _detect("privilege:db_write", "session.add(new_record); session.commit()")
        assert det is not None

    def test_session_delete(self) -> None:
        det = _detect("privilege:db_write", "session.delete(old_record)")
        assert det is not None

    def test_bulk_create(self) -> None:
        det = _detect("privilege:db_write", "User.objects.bulk_create(users)")
        assert det is not None

    def test_mongo_insert(self) -> None:
        det = _detect("privilege:db_write", "collection.insert_one({'name': 'Alice'})")
        assert det is not None

    def test_mongo_delete_many(self) -> None:
        det = _detect("privilege:db_write", "collection.delete_many({'active': False})")
        assert det is not None

    def test_dynamo_put_item(self) -> None:
        det = _detect("privilege:db_write", "table.put_item(Item={'pk': key})")
        assert det is not None

    def test_select_no_match(self) -> None:
        """Plain SELECT should not match."""
        det = _detect("privilege:db_write", 'cur.execute("SELECT * FROM users")')
        assert det is None

    def test_canonical_name(self) -> None:
        det = _detect("privilege:db_write", "session.add(r)")
        assert det is not None
        assert det.canonical_name == "privilege:db_write"


# ---------------------------------------------------------------------------
# privilege:email_out
# ---------------------------------------------------------------------------


class TestEmailOut:
    def test_smtplib_import(self) -> None:
        det = _detect("privilege:email_out", "import smtplib")
        assert det is not None

    def test_smtp_sendmail(self) -> None:
        det = _detect(
            "privilege:email_out",
            "server = smtplib.SMTP('smtp.gmail.com'); server.sendmail(fr, to, msg)",
        )
        assert det is not None

    def test_sendgrid(self) -> None:
        det = _detect(
            "privilege:email_out", "from sendgrid import SendGridAPIClient; sg.send(message)"
        )
        assert det is not None

    def test_ses(self) -> None:
        det = _detect(
            "privilege:email_out", "ses.send_email(Source=FROM, Destination={'ToAddresses': [TO]})"
        )
        assert det is not None

    def test_mailgun(self) -> None:
        det = _detect("privilege:email_out", "import mailgun; mailgun.send({'to': addr})")
        assert det is not None

    def test_resend(self) -> None:
        det = _detect(
            "privilege:email_out", "import resend; resend.Emails.send({'to': to, 'from': fr})"
        )
        assert det is not None

    def test_yagmail(self) -> None:
        det = _detect("privilege:email_out", "import yagmail; yag = yagmail.SMTP(user)")
        assert det is not None

    def test_mime_multipart(self) -> None:
        det = _detect("privilege:email_out", "from email.mime.multipart import MIMEMultipart")
        assert det is not None

    def test_send_email_helper(self) -> None:
        det = _detect("privilege:email_out", "send_email(to=user.email, subject='Welcome')")
        assert det is not None

    def test_canonical_name(self) -> None:
        det = _detect("privilege:email_out", "import smtplib")
        assert det is not None
        assert det.canonical_name == "privilege:email_out"


# ---------------------------------------------------------------------------
# privilege:social_media_out
# ---------------------------------------------------------------------------


class TestSocialMediaOut:
    def test_tweepy(self) -> None:
        det = _detect(
            "privilege:social_media_out",
            "import tweepy; client = tweepy.Client(bearer_token=TOKEN)",
        )
        assert det is not None

    def test_create_tweet(self) -> None:
        det = _detect("privilege:social_media_out", "client.create_tweet(text='Hello world!')")
        assert det is not None

    def test_praw_reddit(self) -> None:
        det = _detect(
            "privilege:social_media_out", "import praw; reddit = praw.Reddit(client_id=CID)"
        )
        assert det is not None

    def test_subreddit_submit(self) -> None:
        det = _detect(
            "privilege:social_media_out", "subreddit.submit(title='Post', selftext='Body')"
        )
        assert det is not None

    def test_discord_send(self) -> None:
        det = _detect("privilege:social_media_out", "await channel.send('Alert: anomaly detected')")
        assert det is not None

    def test_discord_client(self) -> None:
        det = _detect("privilege:social_media_out", "import discord; client = discord.Client()")
        assert det is not None

    def test_telegram_bot(self) -> None:
        det = _detect(
            "privilege:social_media_out",
            "from telegram.ext import Application; bot.send_message(chat_id, text)",
        )
        assert det is not None

    def test_slack_post(self) -> None:
        det = _detect(
            "privilege:social_media_out",
            "client = WebClient(token=SLACK_TOKEN); client.chat_postMessage(channel='#alerts')",
        )
        assert det is not None

    def test_twilio(self) -> None:
        det = _detect(
            "privilege:social_media_out",
            "from twilio.rest import Client; client.messages.create(to=TO, from_=FROM, body=MSG)",
        )
        assert det is not None

    def test_canonical_name(self) -> None:
        det = _detect("privilege:social_media_out", "import tweepy")
        assert det is not None
        assert det.canonical_name == "privilege:social_media_out"


# ---------------------------------------------------------------------------
# privilege:code_execution
# ---------------------------------------------------------------------------


class TestCodeExecution:
    def test_subprocess_run(self) -> None:
        det = _detect(
            "privilege:code_execution",
            "result = subprocess.run(['ls', '-la'], capture_output=True)",
        )
        assert det is not None

    def test_subprocess_popen(self) -> None:
        det = _detect("privilege:code_execution", "proc = subprocess.Popen(cmd, stdout=PIPE)")
        assert det is not None

    def test_subprocess_check_output(self) -> None:
        det = _detect("privilege:code_execution", "out = subprocess.check_output(['git', 'log'])")
        assert det is not None

    def test_os_system(self) -> None:
        det = _detect("privilege:code_execution", "os.system('make build')")
        assert det is not None

    def test_shell_true_flag(self) -> None:
        det = _detect("privilege:code_execution", "subprocess.run(cmd, shell=True)")
        assert det is not None

    def test_bash_tool(self) -> None:
        det = _detect("privilege:code_execution", "tools = [BashTool(), SearchTool()]")
        assert det is not None

    def test_shell_tool(self) -> None:
        det = _detect("privilege:code_execution", "ShellTool(description='Run shell commands')")
        assert det is not None

    def test_e2b_sandbox(self) -> None:
        det = _detect("privilege:code_execution", "from e2b import E2BSandbox; sb = E2BSandbox()")
        assert det is not None

    def test_python_repl_tool(self) -> None:
        det = _detect("privilege:code_execution", "tools.append(PythonREPLTool())")
        assert det is not None

    def test_canonical_name(self) -> None:
        det = _detect("privilege:code_execution", "subprocess.run(['ls'])")
        assert det is not None
        assert det.canonical_name == "privilege:code_execution"


# ---------------------------------------------------------------------------
# privilege:network_out
# ---------------------------------------------------------------------------


class TestNetworkOut:
    def test_requests_post(self) -> None:
        det = _detect(
            "privilege:network_out",
            "resp = requests.post('https://api.example.com/hook', json=payload)",
        )
        assert det is not None

    def test_requests_put(self) -> None:
        det = _detect("privilege:network_out", "requests.put(url, data=body)")
        assert det is not None

    def test_requests_patch(self) -> None:
        det = _detect("privilege:network_out", "requests.patch(endpoint, json=update)")
        assert det is not None

    def test_requests_delete(self) -> None:
        det = _detect("privilege:network_out", "requests.delete(f'{BASE_URL}/resource/{id}')")
        assert det is not None

    def test_httpx_post(self) -> None:
        # Direct httpx.post call — async client alias won't be matched without import context
        det = _detect("privilege:network_out", "response = httpx.post(url, json=data, timeout=30)")
        assert det is not None

    def test_websocket_send(self) -> None:
        det = _detect("privilege:network_out", "await websocket.send(json.dumps(message))")
        assert det is not None

    def test_grpc_channel(self) -> None:
        det = _detect("privilege:network_out", "channel = grpc.insecure_channel('localhost:50051')")
        assert det is not None

    def test_dispatch_webhook(self) -> None:
        det = _detect("privilege:network_out", "dispatch_webhook(url=WEBHOOK_URL, payload=event)")
        assert det is not None

    def test_requests_get_no_match(self) -> None:
        """Read-only GET should NOT trigger the adapter."""
        det = _detect(
            "privilege:network_out", "resp = requests.get('https://api.example.com/data')"
        )
        assert det is None

    def test_canonical_name(self) -> None:
        det = _detect("privilege:network_out", "requests.post(url, json=data)")
        assert det is not None
        assert det.canonical_name == "privilege:network_out"


# ---------------------------------------------------------------------------
# Negative / false-positive guard tests
# ---------------------------------------------------------------------------


class TestNegatives:
    """Ensure adapters don't fire on clearly unrelated code."""

    @pytest.mark.parametrize("canon", list(_ALL_CANON))
    def test_empty_string(self, canon: str) -> None:
        assert _detect(canon, "") is None

    @pytest.mark.parametrize("canon", list(_ALL_CANON))
    def test_hello_world(self, canon: str) -> None:
        assert _detect(canon, 'print("Hello, world!")') is None

    def test_rbac_no_match_on_read_sql(self) -> None:
        assert _detect("privilege:rbac", "SELECT role FROM users WHERE id=1") is None

    def test_admin_no_match_on_admin_panel_string(self) -> None:
        """The word 'admin' alone in a URL string should not match."""
        assert _detect("privilege:admin", 'url = "/admin/dashboard"') is None

    def test_email_no_match_on_email_field(self) -> None:
        """A simple email field definition should not match."""
        assert _detect("privilege:email_out", 'user_email = "alice@example.com"') is None

    def test_network_out_no_match_on_get(self) -> None:
        det = _detect("privilege:network_out", "data = requests.get(url).json()")
        assert det is None
