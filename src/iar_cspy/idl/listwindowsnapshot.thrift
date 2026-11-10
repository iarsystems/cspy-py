namespace cpp CSpy.ListWindow.Snapshot.Thrift
namespace java com.iar.cspy.listwindow.snapshot

include "shared.thrift"
include "listwindow.thrift"

/**
 * Service id used when registering the snapshot service in the service
 * registry.
 */
const string LISTWINDOW_SNAPSHOT_SERVICE = "listwindow.snapshot";

const shared.ServiceVersion LISTWINDOW_SNAPSHOT_VERSION = {
  "major": 1, // Increment for breaking changes
  "minor": 0  // Increment for non-breaking changes
};

/**
 * Read-only access to the contents of the list windows that are open in a
 * hosting IDE.
 *
 * Unlike the ListWindowBackend service, this service never attaches itself
 * to the underlying list model: the IDE's own window stays the sole listener
 * and the client simply observes what the window currently shows. Windows
 * are identified by the model name reported by getOpenWindows(), e.g.
 * "Watch" or "Locals".
 *
 * Sliding (trace-like) windows and unknown ids yield empty results.
 */
service ListWindowSnapshot extends shared.HeartbeatService
{
  /** Get the service version hosted by the server. */
  shared.ServiceVersion GetServiceVersion();

  /** Names of the list windows that are currently open. */
  list<string> getOpenWindows();

  /** Names of all known windows **/
  list<string> getAvailableWindows();

  /** Ensure a window is shown (show = true) or hidden (show = false).
      if focus = true the window is focused for user input
   **/
  void showWindow(1: string id, 2: bool show, 3: bool focus);

  bool isWindowVisible(1: string id);

  /** Human readable name of the window, or "" if unknown. */
  string getDisplayName(1: string id);

  listwindow.ListSpec getListSpec(1: string id);

  list<listwindow.Column> getColumnInfo(1: string id);

  i64 getNumberOfRows(1: string id);

  /** The row at the given index. Out of range indices yield an empty row. */
  listwindow.Row getRow(1: string id, 2: i64 index);
}
