namespace cpp CSpy.ListWindow.Thrift
namespace java com.iar.cspy.listwindow

include "ServiceRegistry.thrift"
include "shared.thrift"


struct Range
{
  1: i64 first,
  2: i64 last
}

enum Alignment
{
  kLeft,
  kRight,
  kCenter
}

enum TextStyle
{
  kFixedPlain,
  kFixedBold,
  kFixedItalic,
  kFixedBoldItalic,
  kProportionalPlain,
  kProportionalBold,
  kProportionalItalic,
  kProportionalBoldItalic
}

enum KeyNavOperation {
  kPrevItem,
  kNextItem,
  kPrevItemPage,
  kNextItemPage,
  kTopItem,
  kBottomItem,
  kNextRight,
  kPrevLeft
}

enum ScrollOperation {
  kScrollLineUp,
  kScrollLineDown,
  kScrollPageUp,
  kScrollPageDown,
  kScrollTop,
  kScrollBottom,
  kScrollTrack
}

enum SelectionFlags {
  kReplace = 0,
  kAdd,
  kRange
}

enum Target
{
  kNoTarget,
  kTargetAll,
  kTargetRow,
  kTargetColumn,
  kTargetCell
}

struct Color
{
  1: i32 r,
  2: i32 g,
  3: i32 b,
  4: bool lowContrast,
  5: bool isDefault
}

struct Format
{
  1: Alignment align,
  2: TextStyle style,
  3: bool editable,
  4: list<string> icons,
  5: Color transp,
  6: Color textColor,
  7: Color bgColor,
  8: Color barColor,
  9: double barFraction
}

struct Cell
{
  1: string text;
  2: Format format;
  3: Target drop;
}

struct Row
{
  1: list<Cell> cells
  2: bool isChecked,
  3: string treeinfo
}

struct Column
{
  1: string title,
  2: i32 width,
  3: bool fixed, // disallow resizing
  4: bool hideSelection, // do not draw selection here
  5: Format defaultFormat
}

struct ListSpec
{
  1: Color bgColor,
  2: bool canClickColumns,
  3: bool showGrid,
  4: bool showHeader,
  5: bool showCheckBoxes
}

struct MenuItem
{
  1: string text,
  2: i32 command,
  3: bool enabled,
  4: bool checked
}

struct SelRange
{
  1: i64 first,
  2: i64 last
}

struct EditInfo
{
  1: string editString,
  2: i32 column
  3: SelRange range;
}

struct Tooltip
{
  1: Target target,
  2: string text
}

struct Drag
{
  1: bool result,
  2: string text,

  // Source coordinates, used when dropping within same window
  3: i64 row,
  4: i32 col
}

struct HelpTag
{
  1: bool available, // ??
  2: string text
}

typedef list<SelRange> Selection

enum What
{
  kEnsureVisible,
  kSelectionUpdate,
  kRowUpdate,
  kNormalUpdate,
  kFullUpdate,
  kFreeze,
  kThaw
}

struct Note
{
  1: What what,
  2: i64 seq,
  3: i64 ensureVisible,
  4: i64 row,
  5: string anonPos
}

enum ToolbarWhat
{
  kNormalUpdate, // Run the state-update
  kFullUpdate,   // Redraw the toolbar from scratch.
  kFocusOn,      // Set focus to a given edit.
}

struct ToolbarNote
{
  1: ToolbarWhat what,
  2: i32 focusOn, // Notify the toolbar to set focus on a given edit.
}

const string SLIDING_POS_NONE = "";

service ListWindowFrontend
{
  oneway void notify(1: Note note);
  oneway void notifyToolbar(1: ToolbarNote note);
}

// State of a toolbar item.
struct ToolbarItemState
{
  1: bool enabled;
  2: bool visible;
  3: bool on;
  4: i64 detail;
  5: string str;
}

/*
 * The ListWindowModel service is a wrapper around the various types of
 * IfListModel-based classes, but with a few changes.
 *
 * - Sequence numbers are not exposed through this interface.
 * - The model lifecycle sequence of events
 *   are handled automatically. In other words: clients just need to
 *   connect to the model and start calling methods. The service will make
 *   sure that the appropriate methods will be called. For example,
 *   any attempt to query the model contents will result in an implicit
 *   show(true) being called.
 * - SetListener/Attach/Detach etc. do not need to be called. "show(false)" may
 *   be called when the corresponding view is hidden.
 * - Freeze/Thaw is also handled automatically. Clients do not need to keep
 *   track of frozen state, but simply update the UI to indicate the state to the user.
 *
 */
service ListWindowBackend extends shared.HeartbeatService
{
  // Connect to the model. This method must be the very first method called on the
  // model, except where otherwise stated.
  void connect(1: ServiceRegistry.ServiceLocation listener),

  // Disconnect from the model.
  void disconnect();

  // Inform the model where it should be storing the data from
  // the SaveContents/RestoreContents calls. Must be called from the
  // client *before* connecting.
  void setContentStorageFile(1: string filename);

  // The "sliding" windows always return a value of 0 for the number of rows,
  i64 getNumberOfRows();

  // Fetch a row from the backend. If the row is out-of-bounds, the list of cells will
  // be empty.
  Row getRow(1: i64 index);

  // Let the model know which rows are visible at the moment. The model should
  // (?) not use this to optimize updates, but rather to do things like saving the
  // current position (if applicable), or "copy current contents to clipboard".
  void setVisibleRows(1: i64 first, 2: i64 last);

  // The UI calls this when the view becomes "visible". Models are expected
  // to trigger a kNormalUpdate in response to this.
  oneway void show(1: bool on),

  list<Column> getColumnInfo(),

  ListSpec getListSpec(),

  i32 toggleExpansion(1: i64 index),

  void toggleCheckmark(1: i64 index),

  list<MenuItem> getContextMenu(1: i64 row, 2: i32 col),

  /*
   * We have to let this be asynchronous, or we risk deadlocks.
   *
   * 1. UI calls handleContextMenu on something which in turn opens a blocking message box
   * 2. In the meantime, there is a UI event which attempts to call e.g. "show()"
   *    on the same listmodel.
   * 3. The blocking message box will now wait for the UI until the show() command completes,
   *    but since the original handleContextMenu command holds the lock to the
   *    listwindows thrift channel, the UI thread will be blocking until handleContextMenu
   *    returns.
   */
  oneway void handleContextMenu(1: i32 command),

  string getDisplayName(),

  i64 scroll(1: ScrollOperation op, 2: i64 first, 3: i64 last),

  void click(1: i64 row, 2: i32 col, 3: SelectionFlags flag),

  void doubleClick(1: i64 row, 2: i32 col),

  EditInfo getEditableString(1: i64 row, 2: i32 col),

  bool setValue(1: i64 row, 2: i32 col, 3: string value),

  Selection getSelection(),

  Tooltip getToolTip(1: i64 row, 2: i32 col, 3: i32 pos),

  bool drop(1: i64 row, 2: i32 col, 3: string text),

  bool dropLocal(1: i64 row, 2: i32 col, 3: string text, 4: i64 srcRow, 5: i32 srcCol),

  Drag getDrag(1: i64 row, 2: i32 col),

  HelpTag getHelpTag(),

  void columnClick(1: i32 col),

  void handleChar(1: i32 c, 2: i32 repeat),

  void handleKeyDown(1: i32 c, 2: i32 repeat, 3: bool shift, 4: bool ctrl),

  void keyNavigate(1: KeyNavOperation op, 2: i32 repeat, 3: i32 flags /* ?? */, 4: i32 rowsInPage),

  void toggleMoreOrLess(1: i64 row),

  Target dropOutsideContent();

  // TODO
  // (string command stuff)

  /*
   * Return true/false if the model is sliding or not. This method can be called before connect().
   */
  bool isSliding();

  ChunkInfo getChunkInfo();

  AddRowsResult addAfter(1: i32 minToAdd, 2: i32 maxToTrim);

  AddRowsResult addBefore(1: i32 minToAdd, 2: i32 maxToTrim);

  NavigateResult navigateToFraction(1: double fraction, 3: i32 chunkPos, 2: i32 minLines);

  NavigateResult navigateTo(1: string toWhat, 3: i32 chunkPos, 2: i32 minLines);

  SelectionResult getSel();

  SelectionResult setSel(1: i32 row);

  i32 keyNav(1: KeyNavOperation op, 2: i32 repeat, 3: i32 rowsInPage);

  // Toolbar stuff
  shared.PropertyTreeItem getToolbarDefinition();
  bool   setToolbarItemValue(1: string id, 2: shared.PropertyTreeItem tree);
  string getToolbarItemValue(1: string id);
  ToolbarItemState  getToolbarItemState(1: string id);
  string getToolbarItemTooltip(1: string id);

}

// Sliding listwindows

struct ChunkInfo {
  1: i32 numberOfRows;
  2: double fractionBefore;
  3: double fractionAfter;
  4: bool atStart;
  5: bool atEnd;
}

// Used for return values of addAfter/addBefore
struct AddRowsResult {
  1: ChunkInfo chunkInfo;
  2: i32 rows;
}

struct NavigateResult {
  1: ChunkInfo chunkInfo;
  2: i32 chunkPos;
}

struct SelectionResult {
  1: i32 row; // -1 if no selection in chunk
  2: string pos; // off-chunk position or SLIDING_POS_NONE
}

// Extensions for the symbolic memory view
service SymbolicMemory extends ListWindowBackend
{
  // Set the current zone, given as a string in the set of zone names
  // returned by getZoneList().
  void setZone(1: string zone),

  // Return the currently selected zone.
  string getZone(),

  // Return a list of zone names for displaying a list of selectable zones
  // to the user. This avoids having the UI know anything about actual
  // zones.
  list<string> getZoneList(),

  void nextSymbol(),

  void prevSymbol(),

  void navigate(1: string expr)
}

service QuickWatch extends ListWindowBackend
{
  oneway void evaluate(1: string expr);
}

service Watch extends ListWindowBackend
{
  void add(1: string expr);
}

service PowerLogSetup extends ListWindowBackend
{
  void setRate(1: i32 sampleRate);
  i32 getRateOfSample();
  i32 getMaxRate();
}

struct StackBarInfo
{
  1: double currentLevel;
  2: double maxLevel;
}

service Stack extends ListWindowBackend
{
  void setStack(1: string name);

  string getStack();

  list<string> getStacks();

  string getBarTooltip();

  StackBarInfo getBarInfo();

  shared.StackSettings getStackSettings();
}

struct TraceCustomParameter
{
  1: string name;
  2: string value;
}

// Thrift-version of DbuTraceFindParams
struct TraceFindParams
{
  1: string findWhat;    // string to search for
  2: bool useRange;      // use address range to narrow search
  3: i64 rangeStart;     // range start
  4: i64 rangeEnd;       // range end
  5: bool textSearch;    // use text search, otherwise use only range
  6: i32 searchColumn;   // column to search, or -1 to search all
  7: string columnName;  // used internally by the MFC view
  8: bool matchCase;     // match case
  9: bool matchWord;     // match whole word

  10: list<string> columns;       // column names to display in dialog
  11: list<string> searchHistory; // search history in dialog

  12: list<TraceCustomParameter> customParameters;
}

struct TraceProgress
{
  1: i32 current;
  2: i32 maxvalue;
}

// API for trace listwindows. This can be reused for most
// kinds of trace, e.g. SWO and ETM, since the toolbars
// are identical.
service TraceListWindowBackend extends ListWindowBackend
{

  // Enable
  bool isEnabled();
  bool canEnable();
  void setEnabled(1: bool on);

  // Clear
  bool canClear();
  void clear();

  // Mixed mode
  bool isMixedMode();
  bool canUseMixedMode();
  void setMixedMode(1: bool on);

  // Save
  bool canSave();
  void save(1: string filename);
  string getDefaultSaveFilename();
  string getDefaultSaveFilenameExt();

  // Find
  bool canFind();
  TraceFindParams getFindParams();
  void find(1: TraceFindParams params);
  void findLocal(1: TraceFindParams params);

  // Browse;
  bool canBrowse();
  bool isBrowsing();
  void setBrowseMode(1: bool on);

  // Progress
  TraceProgress getProgress();

  // Does this driver support trace settings?
  bool supportsTraceSettings();
}

// TODO
// Debugger macros
// Debugger macro registration

struct DragDropFeedback
{
  1: Target target;
  2: i32 rowIdx; // screen index
  3: i32 colIdx; // screen index
}

/*
 * Struct for storing all information necessary to render the
 * listwindow model. This is not used in the service interface itself,
 * but it is useful to have it as a thrift struct for serialization
 * purposes.
 *
 * Note that this does not include transient fields such as "hasFocus",
 * or "frozen".
 */
struct ListWindowRenderParameters
{
  1: ListSpec listSpec;
  2: list<Column> columns;

  // The rows to render. The first row in this list is always the
  // top-most visible line.
  3: list<Row> rows;

  // Currently selected rows.
  4: list<SelRange> selection;

  // Model index of the first row. This is necessary in order to
  // know which offset each row has, when performing actions
  // on it (e.g. setValue()).
  5: i64 offset;

  // If there is any drag feedback to draw.
  6: DragDropFeedback dragDropFeedback;

  // Horizontal scroll-position.
  7: i32 hpos;
}
