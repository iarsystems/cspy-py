/*
 * Thrift interface to replace the old JSON-RPC/WebSockets API (DRAFT)
 */

namespace cpp CMSISPack.Thrift
namespace java com.iar.cmsisagent.thrift

include "shared.thrift"

const string CMSISPACK_AGENT2_SERVICE = "com.iar.cmsisagent2.service";
const string CMSISPACK_AGENT2_EVENTLISTENER_SERVICE = "com.iar.cmsisagent2.eventlistener.service";

// CMSIS pack event topics
const string PROJECT_ADDED = "com.arm.cmsis.pack.rte.project.added";
const string PROJECT_REMOVED = "com.arm.cmsis.pack.rte.project.removed";
const string PROJECT_UPDATED = "com.arm.cmsis.pack.rte.project.updated";
const string PACKS_UPDATED = "com.arm.cmsis.pack.updated";
const string PACKS_RELOADED = "com.arm.cmsis.pack.reloaded";

/*
 
  Protocol versions:
  
  2: EWARM 8.x, using the non-workbench based agent
  3: EWARM ???, using complete workbench with ARM plugins in
 
 */
 
const i32 PROTOCOL_VERSION = 3

enum OutputType
{
  exe,
  lib
}

enum FileCategory
{
  kDoc,
  kHeader,
  kInclude,
  kLibrary,
  kObject,
  kSource,
  kSourceC,
  kSourceCpp,
  kSourceAsm,
  kLinkerScript,
  kUtility,
  kImage,
  kOther
}

// The FileInfo has been upgraded to handle gpdsc files, thus adding a "isGenerated" flag and a fileRelativePath which are 
// needed to resolve the fileInfo for a generated file and add them as includes.
struct FileInfo
{
  1: string name
  2: string attr
  3: FileCategory category
  4: bool isGenerated
  5: string projectRelativePath
}

struct ComponentInfo
{
  1: string deviceClass;
  2: string group;
  3: string vendor;
  4: string version;
  5: string variant;
  6: string sub;
  7: string generator;
  8: string id;
  9: string packId;
  10: string rteComponentsH;
  11: i32 selectedCount;
  12: list<FileInfo> sourceFiles;
}

struct CompileInfo
{
  1: string Pname
  2: string header
  3: string define
}

struct ProcessorInfo
{
  1: string Pname;
  2: string Dvendor;
  3: string Dcore;
  4: string Dfpu;
  5: string Dmpu;
  6: string Dendian;
  7: string Dclock;
  8: string DcoreVersion; 
}

//----------------------------------------------------------------------

struct DeviceInfo
{
  1: string id;
  2: string name;
  3: string packId;
  4: string family;
  5: string vendor;
  6: string subFamily;
  7: string variant;
  8: list<CompileInfo> compile;
  9: list<ProcessorInfo> processor;
}

//----------------------------------------------------------------------

struct ValidationStatus
{
  1: string id;
  2: string result;
  3: bool fulfilled;
  4: string description;
  5: list<ValidationStatus> children;
}

struct Api
{
  1: string componentClass;
  2: string group;
  3: string apiVersion;
  4: string vendor;
  5: string packId;
  6: bool exclusive;
  7: string description;
  8: list<FileInfo> files;
}

struct FileInPack {
  1: string packId;
  2: string subPath;
}

// Struct to hold information about an RTE file in a project
struct RteFile {
  // The pack id to which the file belongs
  1: string packId;

  // The path relative to the project
  2: string projectRelativePath;

  // The absolute path of the pack itself.
  3: string packPath;

  // The relative path within the pack of the file.
  4: string packRelativePath;

  // The component id of the component this file belongs to.
  5: string componentId;

  // The file category
  6: FileCategory category;

  // The "attr" attribute of ICpFileInfo.
  7: string attr;

  // Flag for generated files.
  8: bool isGenerated
}

exception CMSISAgentException 
{
  1: string message;
}

/*
 * New CMSIS agent service to use together with a full CMSIS Pack enabled workbench.
 */
service CMSISAgent2 extends shared.HeartbeatService
{
  /*
   * Loads a project into the manager.
   */
  void loadProject(1: string ewpfile, 2: string rteConfigFile) throws (1: CMSISAgentException e)
  
  /*
   * Configures a new RTE project for the given EW project by opening the
   * device selection dialog for the user to select a device, then proceeding
   * with loading the project by calling "loadProject()".
   */
  void createNewProject(1: string ewpfile, 2: OutputType outputType) throws (1: CMSISAgentException e)
  
  /*
   * Request that the manager shuts down and exits.
   */
  void shutdown() throws (1: CMSISAgentException e)
  
  /*
   * Activate the manager, i.e. attempt to make the window visible.
   */
  void activate(1: string project) throws (1: CMSISAgentException e)
  
  /*
   * Get information about an RTE configuration.
   */
  list<ComponentInfo> getComponentInfo(1: string rte) 
  	throws (1: CMSISAgentException e)

  /*
   * Returns the validation status of an RTE.
   */
  list<ValidationStatus> getValidationStatus(1: string rte) 
  	throws (1: CMSISAgentException e)

  /*
   * Returns the API info of the given RTE.
   */
  list<Api> getApis(1: string rte) 
  	throws (1: CMSISAgentException e)

  /*
   * Returns the device info of the given RTE.
   */
  DeviceInfo getDeviceInfo(1: string rte) throws (1: CMSISAgentException e)
  
  /*
   * Returns the path to the given pack. This is a path inside the local
   * pack repository.
   */
  string getPathToPack(1: string packId) 
  	throws (1: CMSISAgentException e)

  /*
   * Get the pack id from a file, given its project-relative path.
   * Returns empty strings if the path is not inside a pack
   */
  FileInPack getPackIdFromPath(1: string fileInPack) 
  	throws (1: CMSISAgentException e)

  list<RteFile> getRteFiles(1: string projectName) throws (1: CMSISAgentException e)

  /*
   * API calls to invoke UI actions
   */
   
  // Open the "select device" dialog on the given project.
  void openDeviceDialog(1: string projectName) throws (1: CMSISAgentException e)
}

/*
 * An RTE event. This is a thrift-mapping of the corresponding RteEvent type.
 */
struct RteEvent {
  1: string topic;
  3: string projectName;
  4: string ewpFile;
  2: string data;
}

service CMSISEventListener
{
  void onRteEvent(1: RteEvent event);
}
