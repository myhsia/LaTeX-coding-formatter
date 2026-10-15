' Native "cover current version?" confirmation for the MSI.
'
' Scheduled (see format-tex.wxs) only when an older build of the product is
' already installed (WIX_UPGRADE_DETECTED) and the install is interactive
' (NOT UILevel=2), so silent/automation installs never block on a dialog.
'
' OK   -> return 0, the upgrade proceeds (RemoveExistingProducts then runs).
' Cancel -> set a public property so the CoverVersionAbort error action
'          aborts the install cleanly.
Function ConfirmCoverVersion()
    Dim answer
    answer = MsgBox( _
        "A previous version of LaTeX Coding Style Formatter is installed." & _
            vbCrLf & vbCrLf & _
        "Installing this package will overwrite the current version." & _
            vbCrLf & "Continue?", _
        vbOKCancel + vbExclamation, _
        "LaTeX Coding Style Formatter")
    If answer = vbCancel Then
        Session.Property("COVER_VERSION_CANCEL") = "1"
    End If
    ConfirmCoverVersion = 0
End Function
