"""
Script to properly indent callback methods in PersistentBridgeServer
"""
import os

target_path = os.path.abspath("src/execution/bridge_server.py")

with open(target_path, "r", encoding="utf-8") as f:
    text = f.read()

methods = '''
    def _get_status_dict(self) -> Dict[str, Any]:
        """Provides current system and account status for Telegram /status command."""
        st: Dict[str, Any] = {
            "bridge_connected": self.is_ea_connected(),
            "state": "RUNNING" if self.is_ea_connected() else "STANDBY"
        }
        if self.is_ea_connected():
            try:
                acc_resp = self.forward_request_to_ea({"action": "ACCOUNT"}, timeout_sec=3.0)
                if acc_resp.get("status") == "SUCCESS":
                    d = acc_resp.get("data", {})
                    st.update(d)
            except Exception as e:
                logger.debug(f"Account query failed in status callback: {e}")
        return st

    def _enable_trading_action(self) -> bool:
        if not self.is_ea_connected():
            return False
        try:
            res = self.forward_request_to_ea({"action": "ENABLE"}, timeout_sec=5.0)
            return res.get("status") == "SUCCESS"
        except Exception:
            return False

    def _disable_trading_action(self) -> bool:
        if not self.is_ea_connected():
            return False
        try:
            res = self.forward_request_to_ea({"action": "DISABLE"}, timeout_sec=5.0)
            return res.get("status") == "SUCCESS"
        except Exception:
            return False
'''

if "def _get_status_dict(self)" not in text:
    marker = 'if __name__ == "__main__":'
    text = text.replace(marker, methods + "\n\n" + marker)
    with open(target_path, "w", encoding="utf-8") as f:
        f.write(text)
    print("Added callbacks to PersistentBridgeServer class.")
else:
    print("Already present.")
