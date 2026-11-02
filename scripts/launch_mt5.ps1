$p = Start-Process -FilePath "C:\Program Files\MetaTrader 5\terminal64.exe" -ArgumentList '/config:C:\Users\Gackstone_Baraka\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075\config\startup.ini' -WorkingDirectory "C:\Program Files\MetaTrader 5" -PassThru
$p.WaitForExit()
