with open('src/execution/bridge_server.py', 'r', encoding='utf-8') as f:
    text = f.read()

old = '''                            elif req.get("action") == "TRIGGER_SIGNAL":
                                # Trigger local sound and feed alert
                                if sys.platform.startswith("win"):
                                    try:
                                        import winsound
                                        winsound.Beep(1000, 250)
                                    except Exception:
                                        pass
                                line = req.get("line", f"[SIGNAL] Test Signal triggered on {req.get('symbol', 'BTCUSD.m')}")
                                if self.feed_manager:
                                    self.feed_manager.log_event("SIGNAL", line, req.get("symbol", ""), req.get("tf", "M15"))
                                resp = self.forward_request_to_ea(req) if self.is_ea_connected() else {"status": "SUCCESS", "message": "Local signal triggered"}'''

new = '''                            elif req.get("action") in ("SIGNAL", "TRIGGER_SIGNAL"):
                                # Trigger local sound and feed alert
                                if sys.platform.startswith("win"):
                                    try:
                                        import winsound
                                        winsound.Beep(1000, 250)
                                    except Exception:
                                        pass
                                line = req.get("line", f"[SIGNAL] Test Signal triggered on {req.get('symbol', 'BTCUSD.m')}")
                                if self.feed_manager:
                                    self.feed_manager.log_event("SIGNAL", line, req.get("symbol", ""), req.get("tf", "M15"))
                                ea_req = dict(req)
                                ea_req["action"] = "SIGNAL"
                                resp = self.forward_request_to_ea(ea_req) if self.is_ea_connected() else {"status": "SUCCESS", "message": "Local signal triggered"}'''

assert old in text, 'old not found'
text = text.replace(old, new)
with open('src/execution/bridge_server.py', 'w', encoding='utf-8') as f:
    f.write(text)
print('Updated bridge_server.py')
