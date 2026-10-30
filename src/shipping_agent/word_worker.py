"""Isolated Windows Word converter. Macro execution and link updates are disabled."""
import sys
from pathlib import Path


def main():
    import pythoncom
    from win32com.client import DispatchEx
    source, target = Path(sys.argv[1]).resolve(), sys.argv[2]
    formats = {"docx": 16, "doc": 0, "pdf": 17}
    if target not in formats:
        raise ValueError("Unsupported conversion")
    pythoncom.CoInitialize()
    word = None
    try:
        word = DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        word.AutomationSecurity = 3
        word.Options.UpdateLinksAtOpen = False
        doc = word.Documents.Open(str(source), ConfirmConversions=False, ReadOnly=True,
                                  AddToRecentFiles=False, Visible=False)
        try:
            doc.SaveAs2(str(source.with_suffix("." + target)), FileFormat=formats[target],
                        AddToRecentFiles=False)
        finally:
            doc.Close(SaveChanges=0)
    finally:
        if word is not None:
            word.Quit(SaveChanges=0)
        pythoncom.CoUninitialize()


if __name__ == "__main__":
    main()
