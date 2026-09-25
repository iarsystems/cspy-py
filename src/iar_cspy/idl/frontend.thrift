namespace cpp CSpy.Frontend.Thrift
namespace java com.iar.frontend

include "shared.thrift"
include "themes.thrift"

const string FRONTEND_SERVICE = "frontend";

/** Which icon to display in the message box*/
enum MsgIcon {
  kMsgIconInfo,
  kMsgIconQuestion,
  kMsgIconExclaim,
  kMsgIconStop
}

/** What set of buttons to use in the message box*/
enum MsgKind {
  kMsgOk,
  kMsgOkCancel,
  kMsgYesNo,
  kMsgYesNoCancel
}

/** Which button was used to dismiss the message box*/
enum MsgResult {
  kMsgResOk,
  kMsgResCancel,
  kMsgResYes,
  kMsgResNo
}

/** The type of dialog to open*/
enum FileDialogType {
  kOpen,
  kSaveAs
}

/** The set of allowed return types. kExistingFiles
    allows returning multiple file
*/
enum FileDialogReturnType{
  kAny,
  kExistingFile,
  kDirectory,
  kExistingFiles
}

/** Small helper struct for filters. The display name is
*   the readable name of the filter and the files allowed
*   are listed in the filtering vector.
*/
struct FileDialogFilter {
  1: string displayName
  2: list<string> filtering
}

/** The set of allowed options which the backend is expecting.
*/
enum FileDialogOptions{
  kNoOverwritePrompt,
  kFileMustExist,
  kPathMustExist,
  kAllowReturningReadOnlyFile,
  kDontResolveSymlinks
}

enum GenericDialogReturnType{
  kOk,
  kCancel,
  kUnknown,
}

struct GenericDialogResults{
  1: GenericDialogReturnType type,
  2: shared.PropertyTreeItem items,
}

/**
 * Provides frontend services to the C-SPY debugger, such as interactive
 * dialogs, progress monitoring, etc.
 */
service Frontend extends shared.HeartbeatService
{
  /**
   * Opens a dialog box, and waits for it to be dismissed.
   * Only use this method if you actually need the result.
   * If you only want to display an interactive message
   * to the user and don't care about the result, use
   * messageBoxAsync instead.
   *
   * If <tt>dontAskMgrKey</tt> is non-empty, the implementation may use
   * a "don't ask again" button and store the result to avoid
   * showing the dialog again.
   *
   * If the caption is empty, the dialog should use the application's
   * name as caption.
   */
  MsgResult messageBox(1: string msg, 2: string caption, 3: MsgIcon icon, 4: MsgKind kind, 5: string dontAskMgrKey);

  /**
   * Opens a dialog box with a "OK" button. Returns immediately, without
   * waiting for a response.
   */
  oneway void messageBoxAsync(1: string msg, 2: string caption, 3: MsgIcon icon, 4: string dontAskMgrKey);

  /**
   * Open a file dialog. If the user cancels the dialog, an empty list
   * of strings is returned.
   */
  list<string> openFileDialog(1: string title,
                              2: string startdir,
                              3: string filter,
                              4: bool allowMultiple,
                              5: bool existing);

  /**
   * Open a file dialog. If the user cancels the dialog, an empty list
   * of strings is returned.
   */
  list<string> openIHostFileDialog(1: string title,
                                   2: FileDialogType type,
                                   3: FileDialogReturnType returnType,
                                   4: list<FileDialogFilter> filters,
                                   5: list<FileDialogOptions> options,
                                   6: string startdir,
                                   7: string defaultName);

  // Show the file properties for the file.
  oneway void showFileProperties(1: string filePath);

  // Open the file explorer.
  oneway void openFileExplorer(1: string filePath);

  /**
   * Open a directory selection dialog. If the user cancels the dialog, an empty list
   * of strings is returned.
   */
  list<string> openDirectoryDialog(1: string title,
                                   2: bool existing,
                                   3: string startdir);

  /** Title is e.g. <tt>"Save trace data"</tt>
      Filename is e.g. <tt>"trace.txt"</tt>
      Default extension is e.g. <tt>"txt"</tt>
      Start dir is e.g. <tt>"/some/path/on/disk"</tt>
      Filter is e.g. <tt>"Text Files (*.txt)|*.txt|All Files (*.*)|*.*||"</tt>
  */
  list<string> openSaveDialog(1: string title,
                              2: string fileName,
                              3: string defExt,
                              4: string startDir,
                              5: string filter);

  /**
   * Opens a dialog containing a progress bar. Returns
   * an opaque identifier. This method returns immediately;
   * there is no guarantee that the progress bar is actually
   * visible to the user.
   */
  i32 createProgressBar(1: string msg, 2: string caption, 3: i64 minvalue, 4: i64 maxvalue, 5: bool canCancel, 6: bool indeterminate);

  /**
   * Update the progress bar's value. This method is lazy;
   * the progress bar will eventually be updated, unless it is
   * closed before that happens. It returns true if the progress
   * bar has been cancelled.
   */
  bool updateProgressBarValue(1: i32 id, 2: i64 value);

  /**
   * Update the progress bar's message. This method is lazy;
   * the progress bar message will eventually be updated, unless it is
   * closed before that happens. It returns true if the progress
   * bar has been cancelled.
   */
  bool updateProgressBarMessage(1: i32 id, 2: string message);

  /**
   * Close the progress bar. This method is lazy; the progress
   * bar will eventually be closed, but there is no guarantee that
   * it has been closed by the time the method returns.
   */
  void closeProgressBar(1: i32 id);

  void showView(1: string id);

  /**
   * Generic dialog for selecting one element out of a list.
   *
   * @param title The dialog title.
   * @param message Message string displayed above the elements.
   * @param elements The elements to choose from.
   * @return The index of the selected element, or -1 if the dialog was cancelled.
   */
  i32 openElementSelectionDialog(1: string title, 2: string message, 3: list<string> elements);

  /**
  * Generic dialog for selecting multiple elements out of a list.
  */
  list<i32> openMultipleElementSelectionDialog(1: string title, 2: string message, 3: list<string> elements);


  /**
   * Opens the given file in the eclipse/vscode editor
   * @param loc The location to open
   * @param focus If true, also shift focus to the editor
   */
  void editSourceLocation(1: shared.SourceLocation loc, 2: bool focus = true);

  /**
   * Whether the frontend keeps track of aliases between debug sessions on its own. If this returns true,
   * CSpyServer calls `loadAliases` at the start of the session, and does not store any aliases.
   * If this returns false, aliases are stored/restored from a .dnx file in the project's settings directory.
   */
  bool handlesAliasStorage();

  /**
   * Provides the initial file aliases known at the start of a debug session.
   */
  map<string, string> loadAliases();

  /**
   * Resolves the alias for a specified string. The name of the file to be resolved is sent as
   * input and the returning string is the absolute path to the file to be linked with the
   * specified alias id.
   */
  string resolveAliasForFile(1: string fileName, 2: string suggestedFile);

  /**
  *   Resolve the current theme that is used by the client.
  */
  map<themes.ThriftDisplayElement,themes.ColorSchema> getActiveTheme();

  /**
  * Invoke a generic dialog.
  */
  GenericDialogResults invokeDialog(1:string id, 2: string title, 3: shared.PropertyTreeItem entries);

  /**
  *  Handshake before start to establish the set of capabilities in the frontend
  *  and backend. Very similar to the concept in LSP.
  */
  shared.Capabilities getCapabilities();
}
