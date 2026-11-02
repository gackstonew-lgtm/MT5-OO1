with open('mql5/Experts/PythonBridgeEA.mq5', 'r', encoding='utf-8') as f:
    lines = f.readlines()

replacement_lines = [
    "   // 11. ENABLE (Authorize Trading on LIVE / PROP)\n",
    "   if(action == \"ENABLE\")\n",
    "   {\n",
    "      g_trading_enabled = true;\n",
    "      GlobalVariableSet(\"SMC_ENABLED_\" + IntegerToString(InpMagicNumber) + \"_\" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)), 1.0);\n",
    "      Print(\"[PythonBridgeEA] [COMMAND] Trading explicitly ENABLED via command.\");\n",
    "      NotifyUser(\"Trading explicitly ENABLED.\");\n",
    "      UpdateDashboardVisuals();\n",
    "      return \"{\\\"status\\\":\\\"SUCCESS\\\",\\\"message\\\":\\\"Trading explicitly ENABLED\\\",\\\"trading_enabled\\\":true}\";\n",
    "   }\n\n",
    "   // 12. DISABLE (Halt Trading on LIVE / PROP)\n",
    "   if(action == \"DISABLE\")\n",
    "   {\n",
    "      g_trading_enabled = false;\n",
    "      GlobalVariableSet(\"SMC_ENABLED_\" + IntegerToString(InpMagicNumber) + \"_\" + IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN)), 0.0);\n",
    "      Print(\"[PythonBridgeEA] [COMMAND] Trading explicitly DISABLED via command.\");\n",
    "      NotifyUser(\"Trading explicitly DISABLED.\");\n",
    "      UpdateDashboardVisuals();\n",
    "      return \"{\\\"status\\\":\\\"SUCCESS\\\",\\\"message\\\":\\\"Trading explicitly DISABLED\\\",\\\"trading_enabled\\\":false}\";\n",
    "   }\n\n",
]

found = False
for i, line in enumerate(lines):
    if 'Unknown command' in line:
        lines = lines[:i] + replacement_lines + lines[i:]
        found = True
        break

if found:
    with open('mql5/Experts/PythonBridgeEA.mq5', 'w', encoding='utf-8') as f:
        f.writelines(lines)
    print("SUCCESS")
else:
    print("NOT FOUND")
