"""Short per-app notes shown with the first observation of that app."""

APP_NOTES = {
    "com.apple.TextEdit": (
        "New documents are rich text and save as .rtf. If the task wants a plain .txt file, "
        "first choose Format ▸ Make Plain Text (answer OK if it asks). "
        "File ▸ Save… opens a save SHEET in this window. Choosing the folder: press_key "
        "cmd+shift+g while the sheet is open (keys are routed to the panel), then set_value the "
        "PathTextField to the folder path; the 'Where:' popup must then show that folder's name "
        "(press_key return if it doesn't). Then set_value the saveAsNameTextField to the file "
        "name and click the sheet's Save button. Setting a field's value can by itself trigger "
        "the panel's default action — always read the CHANGES before your next step. If a "
        "'replace existing file?' alert appears, it is another sheet in the same window. "
        "Saved means ALL of: the sheet is gone, the window title is the file name, the title "
        "bar no longer says Edited, and the server reports 'DISK: … was written'. "
        "File ▸ Duplicate opens an unsaved copy in a NEW window; save that one — the original "
        "window keeps the original file. "
        "A NEW document can be autosaved into iCloud by itself right after the first edit (its "
        "title becomes 'Untitled.txt' and its document path is under 'Mobile Documents'). That is "
        "not the save the task wants: re-observe, then File ▸ Save As… and choose the folder as "
        "above (don't use Move To…, whose sheet has no path field). Saving moves the file out of "
        "iCloud."
    ),
    "com.apple.finder": (
        "Finder: use View ▸ as List for one row per item; rows can be selected with select_rows. "
        "To enter a folder: select ONLY that folder's row, then press_key cmd+down (with files "
        "selected, cmd+down would open them in their app, in front of the person). Rename: select "
        "the row, press_key return, set_value the focused editor text field, press_key return. Move: "
        "select the files, Edit ▸ Copy, enter the destination folder, Edit ▸ Move Item(s) Here. "
        "The status line ('N items, … available') changes on its own."
    ),
    "com.apple.Stickies": (
        "Stickies has no Save command: it writes a note to disk by itself about 2 s after an edit. "
        "To confirm a save, call wait_for_save on the note's window after editing."
    ),
    "com.apple.Notes": (
        "Notes shows the folder list, the note list and the selected note's body (a text area). "
        "Read long bodies with read_text."
    ),
}
