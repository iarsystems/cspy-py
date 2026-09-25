namespace cpp ProjectManager.Thrift
namespace java com.iar.projectmanager

include "shared.thrift"

// Service identifier
const string PROJECTMANAGER_ID = "com.iar.thrift.service.projectmanager";

enum ProjectManagerErrorType {
    //! A generic error
    Generic
    //! The specified launch configuration is invalid
    LaunchConfigurationInvalid
    //! Loading a launch configuration is not applicable in this context
    LaunchConfigurationNotApplicable
}

exception ProjectManagerError {
  1: string description;
  2: ProjectManagerErrorType errorType;
}

/**
 * Each type describes the role of a tool in a C/C++ project build
 */
enum ToolType
{
  /** A tool accepting C/C++ source files and producing object files */
  Compiler = 1,
  /** A tool accepting asm source files and producing object files */
  Assembler = 2,
  /** A tool accepting object files and producing executable files */
  Linker = 3,
  /**  A tool accepting object files and producing static library files */
  Archiver = 4,
  /** Any tool which does fit in the previous categories (e.g. lexer, output converter) */
  Other = 5
}

/**
 * Each type describes how a tool is invoked during a build (e.g. single input, multi input)
 */
enum InvocationType
{
  /** Tool accepts one input file, produces one output file */
  SingleInput = 1,
  /** Tool accepts multiple input files, produces one output file */
  MultiInput = 2
}

/**
 * Defines a build tool, e.g. a compiler.
 * 
 * A build tool is uniquely identified by its ID, and declares a set of input
 * and output file extensions to specify which files it is able to transform.
 * 
 */
struct ToolDefinition
{
    /** Unique ID of the build tool, stored in projects */
	1: string id;
    /** User-visible name of the tool */
	2: string name;
    /** Name of the executable which implements the tool (e.g. 'iccarm') */
	3: string executableName;
    /** List of file extensions that the tool is able to process (e.g. 'c', 'cpp') */
	4: list<string> inputExtensions;
    /** List of file extensions that the tool is able to produce (e.g. 'o') and are meant for consumption from other tools */
	5: list<string> outputExtensions;
    /** 
    * List of file extensions that the tool is able to produce, but should not be processed by other tools
    * An example is assembler files ('.s') produced by the compiler as a debugging aid.
    */
	6: list<string> hiddenOutputExtensions;
    /** What is the role of this tool in the build? */
	7: ToolType toolType;
    /** How should this tool be invoked? */
	8: InvocationType invocationType;
    /**
    * The semicolon seperated list of user submitted extensions.
    * The list is empty if no extension have been added.
    */
  9: string extensionOverrides;
}

/**
 * Defines a hardware target for which projects can be built for using one or more tools
 * (compiler, linker, etc.).
 */
struct Toolchain 
{
  /** Unique id for the toolchain, persisted in projects*/
	1: string id;
  /** User-visible name of the toolchain */
	2: string name;
  /** The tools used by this toolchain*/
  3: list<ToolDefinition> tools;
  /** The toolkit directory */
  4: string toolkitDir;
  /** The place where templates are stored.*/
  5: string templatesDir;
  /** Is it a CMake toolchain. CMake toolchains are by design not changeable, e.g., extensions etc.*/
  6: bool isCMakeToolchain;
  /** The version of the toolchain on format X.Y.Z. Can be empty*/
  7: string version;
}

/**
 * A build configuration represents a way to build a Project.
 * @see ProjectContext
 */
struct Configuration {
  /**
   * Unique name for this configuration (e.g. 'Debug')
   */
  1: string name;

  /**
   * Id of the Toolchain which handles this configuration (e.g. 'ARM')
   */
  2: string toolchainId;

  /**
   * Indicates whether this is a debug configuration or not.
   */
  3: bool isDebug;

  /**
  *  Indicates that the project is controlled entirely by the CMake
  *  toolchain.
  */
  4: bool isCMakeProject
  
}

/**
 * Corresponds to a workspace on disk
 */
struct WorkspaceContext {
  /** Absolute path of the file where this workspace is persisted*/
  1: string filename;
}

/**
 * Corresponds to a project on disk.
 *
 * This type is both copiable and serializable, as the project manager
 * will always try to reconstruct the project by its path.
 * Therefore it is desirable to copy context instances instead of
 * holding references or pointers to an existing context owned by
 * another class, which might disappear and crash the application.
 */
struct ProjectContext {
  /** Absolute path of the file where this project is persisted*/
  1: string filename;
}

/** Element types in a project tree */
enum NodeType
{
  Invalid = 0,
  Group = 1,
  File = 2,
  ControlFile = 3, // Will automatically trigger an update if the file is valid.
  ExternBinary = 4,
  AuxExternBinary = 5,
  CMakeExecutableGroup = 6,
  CMakeLibraryGroup = 7,
}

/** A view of an element in a project tree and its children. <p>
 *  This can be modified on the client side and is not persisted in the actual project
 *  until the backend is instructed to save this element.
 *
 * @see ProjectManager.SetNodeByIndex()
 */
struct Node
{
  /** Name of this node, unique within the whole project. */
  1: string name;
  /** Children nodes of this node. */
  2: list<Node> children;
  /** Type of this node: group, file. */
  3: NodeType type;
  /** Path, if any, of the file system resource referred by this node. Might include arg vars as $PROJ_DIR$, etc. */
  4: string path;
  /** Node is enabled for Multi File Compilation. */
  5: bool isMfcEnabled;
  /** Node is excluded from build. */
  6: bool isExcludedFromBuild;
  /** Node has overriding settings. */
  7: bool hasLocalSettings;
  /** Node has overriding settings and these are in effect. */
  8: bool hasRelevantSettings;
  /** Node has at least one child with overriding settings and these are in effect. */
  9: bool childrenHaveLocalSettings;
  /** True for nodes that are generated (typically during build). */
  10: bool isGenerated;
  /** Names of the plugins using this file as a control file */
  11: list<string> controlFilePlugins;
}

/** A build step, i.e. a command line that takes some input file(s) and produces
 * some output file(s).
 */
struct BuildNode
{
  /** Paths to the input files (the dependencies). */
  1: list<string> input
  /** Paths to the files created by the node. */
  2: list<string> output
  /** The command line used to run the node. */
  3: list<string> arguments
  /** The working directory to run the command line in. */
  4: string directory
  /** The name of the tool that created the node, or a generic descriptor for the type of the node.
   *  See CdlTags.h for common descriptor values.
   */
  5: string toolName
}

/** An option type describes which control should be used to manipulate the option in a GUI
 *
 * Most of these currently match the class names in the SWTD option system, as that is the
 * only option type system which is widely used. However, DeviceSelection represents both
 * EditMenu and CMSIS Pack device selectors.
 * 
 */
enum OptionType {
  Check = 0,
  Edit = 1,
  EditB = 2,
  List = 3,
  Radio = 4,
  CheckList = 5,
  BuildActions = 6,
  DeviceSelection = 7,
  CMSISDevice = 8,
  CMakeSettings = 9,
  PathList = 10,
  EditHexInteger = 11,
  JsonKeyValueMap = 12,
  CheckTree = 13,
}

/** Contains information about an available C-SPY plugin
 *
 * such as id, description, and current loading status. Ports the
 * C++ type PmConfiguration::Plugin to Thrift.
 */
struct DebuggerPlugin {
    1: string name
    2: string description
    3: string path
    4: string originator
    5: string version
    6: bool   currentLoad
    7: bool   factoryLoad
    8: string keywordRoot
}

enum BuildSequence {
  PreBuild = 0,
  PostBuild = 1,
}

/** Properties of an option's element (e.g. list item, radio button, a checkbox in a check list) */
struct OptionElementDescription
{
   /** Unique id of the option element */
   1: string id;

   /** User-readable label for the option element */
   2: string label;

   /** Whether the element is enabled/selectable in the GUI.
   *   It is up to the client to determine if the element should be invisible, or disabled.
   */
   3: bool enabled;

   /** Additional data for this element.
    *  Depnding on the containing option type, this might be e.g. device menu file paths
    */
   4: string data;

   /** A textual, user-readable description for the element. Can be empty if not available. */
   6: string description;

   /** A list of subelements for the element.
    *  Is used when generating tree-like structures in the UI.
    */
   5: list<OptionElementDescription> elements;
}

/** Properties of an option */
struct OptionDescription
{
   /** Unique id of the option */
   1: string id;
   
   /** String value of the option, as persisted in the .ewp file */
   2: string value;
   
   /** Type of the option */
   3: OptionType type;

   /** A list of elements in this option (list elements, checkboxes (?), etc.) */
   4: list<OptionElementDescription> elements;

   /** Whether this option is enabled in the GUI */
   5: bool enabled;

   /** Whether this option is visible in the GUI */
   6: bool visible;

   /** Whether this option can be overridden locally for a project node */
   7: bool canBeLocal;

   /** Whether this option's value should match the corresponding option in the parent settings.
       This is only meaningful for local options, i.e. belonging to a node. 
     */
   8: bool inherited;

   /** A textual, user-readable description for the option. Can be empty if not available. */
   9: string description;
}

/** Associates a group of options under a single category. */
struct OptionCategory
{
   /** Unique id of the option (settings) category */
   1: string id;
   
   /** List of option IDs which are included in this category */
   2: list<string> optionIds;
}

/** Input to builds. */
struct BuildItem
{
   /** The project to build. */
   1: ProjectContext projectContext;

   /** The configuration in that project to build. */
   2: string configurationName;

   /** File path to specific nodes. Empty for full project builds. */
   3: list<string> nodePaths;
}

struct BatchBuildItem
{
   /** The name of the batch. */
   1: string name;

   /** Build items included in batch. */
   2: list<BuildItem> buildItems;
}

/** Enumeration describing different file sets */
enum FileCollectionType
{
  ProjFiles = 0, 
  ProjAndUserIncludeFiles = 1,
  ProjAndAllIncludeFiles = 2,
  WsFiles = 3,
  WsAndUserIncludeFiles = 4,
  WsAndAllIncludeFiles = 5
}

/** Stores the result of a build, referring to the project that was built */
struct BuildResult
{
   /** The project which was built */
   1: ProjectContext projectContext;

   /** Output from the build tools */
   2: list<string> buildOutput;

   /** Whether the build terminated successfully */
   3: bool succeded;
}

/** A simple representation of the control file plugins to use in GUI:s*/
struct ControlFilePlugin
{
  /** The name of the plugin*/
  1: string name
  /** The filter to use in file selection*/
  2: string filefilter
  /** Whether this plugin should be hidden from the project connection menu*/
  3: bool isInternal
}

/** Desktop Path Platforms */
enum DesktopPathPlatform
{
    Mfc = 0,
    Qt = 1
}
enum DesktopPathSlavery
{
    Master = 0,
    Slave = 1
}

/** Settings for user arg vars. Replaces PmUserArgVarModifier */
struct UserArgVarInfo {
  1: string name;
  2: string value;
  3: i32  id;
}

/** Possible categories of user arg var settings */
enum UserArgVarCategory { 
  /** Settings which are specific to the current workspace */
  kWorkspace = 0, 
  /** Settings which are global for the current user */
  kGlobal = 1 
}

/** Settings for user arg var groups */
struct UserArgVarGroupInfo {
  1: string name;
  2: bool active;
  3: bool readOnly;
  4: bool inherited;
  5: UserArgVarCategory category;
  6: i32  id;
  7: list<UserArgVarInfo> variables;
}

/** A build tool provided to the project manager by a client */
struct ExternalTool {
  1: string name;
  2: string path;
  3: string arguments;
  4: string positionRegexp;
  5: string warningRegexp;
  6: string errorRegexp;
}

/** Abstract description of the wizards available */
struct WizardPlugin {
  1: string toolchainName;
  2: string displayName;
  3: string description;
  4: bool requireSave;
  5: string groupName;
}

/** A description of the current information stored in the debug launcher */
struct DebugLauncherInfo{
  1: string launchFile;
  2: string launchName;
  3: bool isEnabled;
  4: i64 latestReloadTime;
}

/** A service which manages Embedded Workbench project files (.ewp) 
 *
 * It can manipulate the project nodes and build configurations, as well as reading/writing 
 * their respective settings. 
 *
 * It also holds the one and only workspace. 
 * For project operations the workspace is implicit (no context parameter needed).
 * If project operations are made without creating or loading any workspace first
 * an 'anonymous' workspace is created. An 'anonymous' workspace cannot be saved.
 *
 * There is also experimental support to register new toolchains directly from the service, without
 * requiring an swtd library. This is however very limited as of now in that there is
 * no option support for the tools in the toolchain.
 */
service ProjectManager extends shared.HeartbeatService
{
  /** Create new, empty workspace with the provided file path. */
  /** Creating a workspace will automatically close any open workspace. */
  /** The empty workspace file is saved. Any missing directory in the path */
  /** will be created. */
  WorkspaceContext CreateEwwFile(1:string file_path) throws (1:ProjectManagerError e);
  
  /** Disable the storing of data into the wsdt file used mostly by MFC side */
  void DisableAutoDataStoring();

  /** Load workspace from .eww file. Projects in the workspace will also be loaded. */
  /** Loading a workspace will automatically close any open workspace. */
  /** Supplying an empty path will create an empty workspace and not attempt to load anything. */
  /** If fetchDependencyData is true, populates the project tree with header dependencies and generated outputs. */
  WorkspaceContext LoadEwwFile(1:string file_path, 2:bool fetchDependencyData) throws (1:ProjectManagerError e);

  /** Check if a workspace exists */
  bool HasWorkspace();

  /** Returns true is there is cached data that is not saved. */
  bool IsWorkspaceModified();
  
  /** Save workspace to file. */
  /** There is always only one workspace so no context is needed. */
  void SaveEwwFile() throws (1:ProjectManagerError e);

  /** Save workspace to new file. */
  void SaveEwwFileAs(1:string file_path) throws (1:ProjectManagerError e);
  
  /** Get all projects in a workspace. */
  /** There is always only one workspace so no context is needed. */
  list<ProjectContext> GetProjects();

  /** Get all successfully loaded projects in a workspace. */
  /** There is always only one workspace so no context is needed. Requires a target. */
  list<ProjectContext> GetLoadedProjects();

  /** Get current project. */
  /** If no project has been set as current the returned context will have an empty filename. */
  ProjectContext GetCurrentProject() throws (1:ProjectManagerError e);

  /** Set current project. */
  /** This change is saved to the settings file. */
  void SetCurrentProject(1:ProjectContext ctx) throws (1:ProjectManagerError e);

  /** Close loaded workspace, freeing the resources allocated for it by the project manager. */
  void CloseWorkspace() throws (1:ProjectManagerError e);

  /** Create new, empty project with the provided file path*/
  ProjectContext CreateEwpFile(1:string file_path) throws (1:ProjectManagerError e);

  /** Create new, empty project with the provided file path and toolchain.
   * The project will contain "Debug" and "Release" build configurations.
   */
  ProjectContext CreateEwpFileWithToolChain(1:string file_path, 2:string toolchain) throws (1:ProjectManagerError e);

  /** Create new project from a template */
  ProjectContext CreateProjectFromTemplate(1:string template_path, 2:string project_path) throws (1:ProjectManagerError e);

  /** Load project from .ewp file. 
   * If fetchDependencyData is true, populates the project tree with header
   * dependencies and generated outputs.
   */
  ProjectContext LoadEwpFile(1:string file_path, 2: bool fetchDependencyData) throws (1:ProjectManagerError e);
  
  /** Save project to file specified in the context */
  void SaveEwpFile(1:ProjectContext project) throws (1:ProjectManagerError e);

  /** Reload project that has for example been modified on disk. */
  ProjectContext ReloadProject(1:ProjectContext project, 2:bool fetchDependencyData) throws (1:ProjectManagerError e);

  /** Save a copy of the project to a new file.
  * 
  * Note that the new project needs to be opened separately if needed.
  */
  void SaveEwpFileAs(1:ProjectContext project, 2:string file_path) throws (1:ProjectManagerError e);

  /** Imports the files of a given project file to the given project. */
  bool ImportProjectFiles(1:ProjectContext ctx, 2:string file_path) throws (1:ProjectManagerError e);

  /** Returns true if there is cached data that is not saved. */
  bool IsModified(1:ProjectContext project);

  /** Sets the modified state of a project. Returns the previous state. */
  bool SetModified(1:ProjectContext project, 2: bool modified);

  /** Returns true if the given configuration is busy and is not allowed to be modified **/
  bool IsLocked(1:ProjectContext project, 2:string configurationName);

  /** Returns true if the given file is a member of current project. */
  bool IsMemberOfCurrentProject(1:string file_path);

  /** Returns a list of files matching file_path. Header files */
  /** if input file is a source file and source files if it is a header file. */
  list<string> FindMatchingHeaderOrSourceFile(1:string file_path);

  /** Get existing project context given file path.
      Throws a ProjectManagerError if the project has not been previously loaded
   */
  ProjectContext GetProject(1:string file_path) throws (1:ProjectManagerError e);

  /** Close loaded project, freeing the resources allocated for it by the project manager */
  /** It only has any effect on anonymous workspaces. */
  void CloseProject(1:ProjectContext project) throws (1:ProjectManagerError e);

  /** Remove the project from the workspace. This works on any workspace as opposed to CloseProject. */
  void RemoveProject(1:ProjectContext project) throws (1:ProjectManagerError e);

  /** Returns a list of files from the given project and configuration. */
  /** It works on the current workspace. Parameters project and configurationName */
  /** are ignored for workspace wide collections and configurationName is ignored */
  /** on the project wide collection. */
  list<string> GetFiles(1:ProjectContext project, 2: string configurationName, 3: FileCollectionType col) throws (1:ProjectManagerError e);
  
  /** Add a Configuration to a project */
  void AddConfiguration(1: Configuration config, 2: ProjectContext project, 3: bool isDebug);

  /** Add a Configuration to a project */
  /** Does not save the project. */
  void AddConfigurationNoSave(1: ProjectContext project, 2: Configuration config, 3: string basedOnName);

  /** Remove a Configuration from a project given its name*/
  void RemoveConfiguration(1: string configurationName, 2: ProjectContext project);

  /** Remove a Configuration from a project given its name*/
  /** Does not save the project. */
  void RemoveConfigurationNoSave(1: ProjectContext project, 2: string configurationName);

  /** Get all Configurations in a project */
  list<Configuration> GetConfigurations(1: ProjectContext project);

  /** Set the order of all Configurations in a project */
  /** This will NOT save the project. */
  /** If any configuration name is supplied that does not match what */
  /** is in the project, those are ignored. */
  /** If the given list is not complete, the remaining configurations */
  /** are put last in the order they are. */
  void SetConfigurationsOrder(1: ProjectContext project, 2: list<string> configNames);

  /** Get current configuration. */
  Configuration GetCurrentConfiguration(1: ProjectContext project) throws (1:ProjectManagerError e);

  /** Set current project and configuration. */
  /** This change is saved to the settings file. */
  void SetCurrentConfiguration(1: ProjectContext project, 2:string configurationName) throws (1:ProjectManagerError e);

  /** Set current configuration for multiple projects and the last project as current project.
      This change is saved to the settings file. */
  void SetCurrentConfigurations(1: list<ProjectContext> projects, 2:list<string> configurationNames) throws (1:ProjectManagerError e);

  /** Create a working copy of an existing configuration, for use in interactive editing.
    * Working copies only exist in memory and are only persisted when applied to their original config.
    * @return a unique working copy id, to be used in ApplyConfigWorkingCopy and DiscardConfigWorkingCopy
    */
  string CreateConfigWorkingCopy(1: ProjectContext project, 2: string originalConfigName);

  /** Create a working copy of a node local settings in an existing configuration, for use in interactive editing.
    * Working copies only exist in memory and are only persisted when applied to their original config.
    * @return a unique working copy id, to be used in ApplyConfigWorkingCopy and DiscardConfigWorkingCopy
    */
  string CreateConfigNodeWorkingCopy(1: ProjectContext project, 2: string originalConfigName, 3: Node node);

  /** Apply the changes of a configuration working copy to its original configuration */
  void ApplyConfigWorkingCopy(1: ProjectContext project, 2: string workingCopyId);

  /** Discard the changes of a configuration working copy. Its id will be invalid when this call returns. */
  void DiscardConfigWorkingCopy(1: ProjectContext project, 2: string workingCopyId);

  /** Desktop path parameters */
  void SetDesktopPathParameters(1: DesktopPathPlatform platform, 2: DesktopPathSlavery slavery);

  /** Get the path to the off-line desktop settings file for the workspace **/
  string GetOfflineDesktopPath();

  /** Get the path to the on-line desktop settings file for the current project **/
  string GetOnlineDesktopPath();

  /** Get the root of a project's file and group hierarchy tree, including all children.
   *  The returned node is not specific to any build configuration. Use GetNodeByIndexAndConfig()
   *  to retrieve the root node of a specific build configuration.
   */  
  Node GetRootNode(1: ProjectContext ctx);

  /** Set a node in the project's hierarchy, possibly replacing an existing subtree if a node with the same name already exists (e.g. the project root). */
  /** @deprecated, only provided for backwards compatibility with old VSCode plugins. */
  void SetNode(1: ProjectContext ctx, 2: Node node);

  /** Get a specific node given its index path in the project's current configuration */
  /** nodeIndexPath is a tree index path: */
  /** Example {0, 3, 2} means 2nd item in the 3rd item of item 0 (0 based indexing) */
  /** If the node is not part of the project, or if the project cannot be found, the returned node has type == Invalid.
   */
  Node GetNodeByIndex(1: ProjectContext ctx, 2: list<i64> nodeIndexPath);

  /** Get a specific node given its index path in the provided build configuration */
  /** nodeIndexPath is a tree index path: */
  /** Example {0, 3, 2} means 2nd item in the 3rd item of item 0 (0 based indexing) */
  /** If the node is not part of the project, returned node has type == Invalid. */
  Node GetNodeByIndexAndConfig(1: ProjectContext ctx, 2: list<i64> nodeIndexPath, 3: string configName);

  /** Set a specific node given its index path. */
  /** Only use this for a large update of the whole tree. For individual node operations */
  /** use the Add/Update/Remove operations.
  /** nodeIndexPath is a tree index path: */
  /** Example {0, 3, 2} means 2nd item in the 3rd item of item 0 (0 based indexing) */
  /** Node will contain any replacement data for the node including all children below it. */
  /** If save is true, the changes will be saved to disk otherwise project stay modified. */
  /** If the node is not part of the project an exception is thrown. */
  void SetNodeByIndex(1: ProjectContext ctx, 2: list<i64> nodeIndexPath, 3: Node node, 4: bool save) throws (1:ProjectManagerError e);

  /** Add a child to a specific node given its index path. */
  /** No assumption hall be made on where in the list of childrens the node is added. */
  /** If save is true, the project change will be saved to disk otherwise project stay modified. */
  /** If the node is not part of the project an exception is thrown. */
  void AddNodeByIndex(1: ProjectContext ctx, 2: list<i64> nodeIndexPath, 3: Node node, 4: bool save) throws (1:ProjectManagerError e);

  /** Remove a specific node, including any subtree, given its index path. */
  /** If it is a node representing a file, it will be removed from the proect but the file */
  /** will not be deleted on disk.
  /** If save is true, the project change will be saved to disk otherwise project stay modified. */
  /** If the node is not part of the project an exception is thrown. */
  /** Some nodes cannot be removed - just silently ignored then */
  void RemoveNodeByIndex(1: ProjectContext ctx, 2: list<i64> nodeIndexPath, 3: bool save) throws (1:ProjectManagerError e);

  /** Updates a specific node given its index path. */
  /** Only the given node is updated, the Node.children list is ignored. */
  /** Currently we only support name change on group nodes, as well as toggling (auxiliary) external binary tag for files. */
  /** If save is true, the project change will be saved to disk otherwise project stay modified. */
  /** If the node is not part of the project an exception is thrown. */
  void UpdateNodeByIndex(1: ProjectContext ctx, 2: list<i64> nodeIndexPath, 3: Node node, 4: bool save) throws (1:ProjectManagerError e);

  /** Determine whether a node can be moved to another. */
  bool CanMoveNode(1: ProjectContext ctx, 2: list<i64> srcNodeIndexPath, 3: list<i64> dstNodeIndexPath ) throws (1:ProjectManagerError e);

  /** Move a node to another. */
  /** If for some reason the move canot be done, false is returned. */
  bool MoveNode(1: ProjectContext ctx, 2: list<i64> srcNodeIndexPath, 3: list<i64> dstNodeIndexPath ) throws (1:ProjectManagerError e);

  /** Get tool chain extensions for the current configuration. */
  /** The list is either empty or it has three strings in this order: */
  /** compiler extensions */
  /** assembler extensions */
  /** linker extensions */
  /** Within each string [:;, ] are used as separators */
  list<string> GetToolChainExtensions(1: ProjectContext ctx);

  /** Get a list of available Toolchains. 
  *
  * Note that as of now the retrieved toolchains cannot provide a complete list of tools.
  * Prior to IDE 9.3, the tools list is empty. In later versions, it contains only a
  * compiler and assembler (if available), and the definitions for those tools only 
  * populate the 'toolType' and 'id' fields.
  * A workaround is that the option categories ids usually match
  * the tool IDs, so they can be used as such in e.g. GetToolCommandLineForConfiguration(). See MAJ-114
  */
  list<Toolchain> GetToolchains() throws (1:ProjectManagerError e);

  /** Register a toolchain with the project manager. Will fail if the toolchain is already registered. */
  void AddToolchain(1:Toolchain toolchain) throws (1:ProjectManagerError e);

  /** Update the definition for a tool */
  bool UpdateTool(1: string toolchainId, 2: ToolDefinition tool);

  /** Get batch build item list. */
  list<BatchBuildItem> GetBatchBuildItems() throws (1:ProjectManagerError e);

  /** Set batch build item list. */
  /** Workspace is in modified state after this method is aclled unless the list is */
  /** the same as the one already stored. */
  void SetBatchBuildItems(1: list<BatchBuildItem> batchBuildItems) throws (1:ProjectManagerError e);

  /** Build a project configuration synchronously, and return its result */
  BuildResult BuildProject(1: ProjectContext prj, 2: string configurationName, 3: i32 numParallelBuilds) throws (1:ProjectManagerError e);

  /** Rebuilds project configurations asynchronously */
  void RebuildAllAsync(1: list<BuildItem> buildItems, 2: bool stopAtError, 3: i32 numParallelBuilds) throws (1:ProjectManagerError e);

  /** Returns whether the build item can be compiled */
  bool CanCompile(1: BuildItem buildItem) throws (1:ProjectManagerError e);

  /** Compile a set of files asynchronously */
  void CompileAsync(1: BuildItem buildItem, 2: i32 numParallelBuilds) throws (1:ProjectManagerError e);

  /** Builds project configurations asynchronously */
  void BuildAsync(1: list<BuildItem> buildItems, 2: bool stopAtError, 3: i32 numParallelBuilds) throws (1:ProjectManagerError e);

  /** Clean project configurations asynchronously */
  void CleanAsync(1: list<BuildItem> buildItems) throws (1:ProjectManagerError e);

  /** Stop an ongoing build. */
  void CancelBuild();

  /** Stop an ongoing static code analysis. */
  void TerminateAnalysis();

  /** Get a list of all build nodes describing how to build the given project configuration. */
  list<BuildNode> GetBuildNodes(1: ProjectContext prj, 2: string configurationName, 3: string toolIdentifier);

  /** Get a list of options for the given node (file, group) in a project, within the given configuration . */
  list<OptionDescription> GetOptionsForNode(1: ProjectContext prj, 2: Node node, 3: string configurationName, 4: list<string> optionIds = []) throws (1:ProjectManagerError e);

  /** Get a list of options for the given build configuration in a project. */
  list<OptionDescription> GetOptionsForConfiguration(1: ProjectContext prj, 2: string configurationName, 3: list<string> optionIds = []) throws (1:ProjectManagerError e);
  
  /** Set a list of options for the given node (file, group) in a project. Return a list of updated options.*/
  list<OptionDescription> ApplyOptionsForNode(1: ProjectContext prj, 2: Node node, 3: string configurationName, 4: list<OptionDescription> optionsToSet) throws (1:ProjectManagerError e);

  /** Remove all options specific to a node. Has no effect if the node has no options. */
  void RemoveOptionsForNode(1: ProjectContext prj, 2: Node node, 3: string configurationName) throws (1:ProjectManagerError e);
  
  /** Set a list of options for the given node (file, group) in a project without saving to the EWP file. Return a list of updated options.*/
  list<OptionDescription> VerifyOptionsForNode(1: ProjectContext prj, 2: Node node, 3: string configurationName, 4: list<OptionDescription> optionsToSet) throws (1:ProjectManagerError e);
  
  /** Set a list of options for the given build configuration in a project. Return a list of updated options.*/
  list<OptionDescription> ApplyOptionsForConfiguration(1: ProjectContext prj, 2: string configurationName, 3: list<OptionDescription> optionsToSet) throws (1:ProjectManagerError e);

  /** Set a list of options for the given build configuration in a project without saving to the EWP file. Return a list of updated options.*/
  list<OptionDescription> VerifyOptionsForConfiguration(1: ProjectContext prj, 2: string configurationName, 3: list<OptionDescription> optionsToSet) throws (1:ProjectManagerError e);

  /** Get a list of option categories for a given configuration */
  list<OptionCategory> GetOptionCategories(1: ProjectContext prj, 2: string configurationName)

  /** Compares several lists of options, by:
    * - computing a set containing all the options ids in each list
    * - comparing the string value of options that have the same id. If an option is missing from an input list, it is considered as having an empty value in that list.
    * - building a map from option ids to a list of values that differ
    *
    * @return a map of option ids to string tuples for the options that differ (as lists containing exactly n values for n input lists). 
    *         If the result is an empty map, then the lists of options are identical.
    *         Any option that are missing from one of the input lists will be considered as having an empty string value.
    */
  map<string, list<string>> CompareOptions(1: list<list<OptionDescription>> optionsToCompare);

  /** Enable/disable multi-file compilation for the provided project, configuration and project node */
  void EnableMultiFileCompilation(1: ProjectContext prj, 2: string configurationName, 3: Node node, 4: bool enabled)

  /** Enable/disable multi-file 'discard public symbols' for the provided project, configuration and project node */
  void EnableMultiFileDiscardPublicSymbols(1: ProjectContext prj, 2: string configurationName, 3: Node node, 4: bool enabled)

  /** Returns whether multi-file compilation is enabled for the provided project, configuration and project node */
  bool IsMultiFileCompilationEnabled(1: ProjectContext prj, 2: string configurationName, 3: Node node)

  /** Returns whether multi-file 'discard public symbols' is enabled for the provided project, configuration and project node */
  bool IsMultiFileDiscardPublicSymbolsEnabled(1: ProjectContext prj, 2: string configurationName, 3: Node node)

  /** Get a list of debugger plugins for the given build configuration in a project. */
  list<DebuggerPlugin> GetDebuggerPlugins(1: ProjectContext prj, 2: string configurationName) throws (1:ProjectManagerError e);
  
  /** Activate/deactivate a debugger plugin for the given build configuration in a project given the plugin name.
    * @return whether the plugin state changed 
    */
  bool SetDebuggerPluginActive(1: ProjectContext prj, 2: string configurationName, 3: string pluginName, 4: bool active) throws (1:ProjectManagerError e);
  
  /** Get the command line arguments of a tool given a build configuration in a project.
    Note that GetToolchains() cannot currently provide information about the tools in a toolchain, so the tool
    ids must be either known in advance, or assumed to match option category ids. See MAJ-114
  */
  list<string> GetToolArgumentsForConfiguration(1: ProjectContext prj, 2: string toolId, 3: string configurationName)

  shared.LaunchConfiguration GetLaunchConfigurationForConfiguration(1: ProjectContext prj, 2: string configurationName) throws (1:ProjectManagerError e);

  /** Expand all argument variables ('argvar' e.g. $TOOLKIT_DIR$) in the provided input string, 
     given the current workspace and provided project and build configuration
  */
  string ExpandArgVars(1: string input, 2: ProjectContext project, 3: string configurationName, 4: bool throwOnFailure)

  /**
  * Gets a JSON representaion of the option presentation
  *
  * Supported locales: en_GB
  */
  string GetPresentationForOptionsAsJson(1:string locale) throws (1:ProjectManagerError e);


  /** Update all project connections listed for the given project. */
  void UpdateProjectConnections(1: ProjectContext prj)

  /** Run the update sequence for a specific file. */
  bool UpdateProjectConnection(1: ProjectContext prj, 2: string file)

  /** Remove all created monitors for a given project. */
  void RemoveMonitors(1: ProjectContext prj)
  
  /** Enable/disable the usage of project connection files. */
  void EnableProjectConnections(1: bool enable)

  // Check if the external project has been updated and needs 
  // to be synced again.
  bool IsExternalProjectUpToDate(1: ProjectContext prj)

  // Synchrize the externa project based on the current state.
  bool SynchonizeExternalProject(1: ProjectContext prj, 2: BuildSequence seq = BuildSequence.PostBuild)

  // Ask the external project to configure itself. Set force to true to
  // remove the current model and redo everything.
  bool ConfigureExternalProject(1: ProjectContext prj, 2: bool force)

  // Ask for a CMake build directory, and attach the project to it.
  bool SelectAndAttachCMakeBuildDir(1: ProjectContext prj)

  // Attach the project to a build directory.
  bool AttachCMakeBuildDir(1: ProjectContext prj, 2: string buildDir)

  // Ask for a root CMakeLists.txt, and try to import it.
  bool SelectAndImportCMakeLists(1: ProjectContext prj)

  /** Add a control file for a specific plugin. Throws if:
      1. The supplied plugin does not exist.
      2. The supplied plugin does not accept the given file.
  */
  void AddControlFile(1: ProjectContext prj, 2: string file, 3: string pluginId)

  /** Check if the project has a control file node registered for a given plugin.*/
  bool HasControlFileFor(1: ProjectContext prj, 2: string pluginId)

  /** Check if the project is a CMake project */
  bool IsCMakeProject(1: ProjectContext prj);

  /** Check if the project is an empty CMake project */
  bool IsEmptyCMakeProject(1: ProjectContext prj);

  /** Clear all listed models to reset the CMake project */
  void ResetCMakeProject(1: ProjectContext prj)

  /** Check if project connection is enabled. */
  bool IsProjectConnectionsEnabled()

  /** Get a list with information about the set of registered control files plugins. */
  list<ControlFilePlugin> GetControlFilePlugins()

  /** Get a list of options at project-level, possibly specifying which option ids to retrieve.
      An empty list will cause all project-level options to be retrieved.
    */
  list<OptionDescription> GetOptionsForProject(1: ProjectContext prj, 2: list<string> optionIds = []);

  /** Apply the provided project-level options to the project.
    * Returns the options that were actually set and their values.
    *
    * Note that this function returned a bool up to platform 9.3.x. It has been changed in 9.4 to return
    * the list of options that have actually changed.
    */
  list<OptionDescription> ApplyOptionsForProject(1: ProjectContext prj, 2: list<OptionDescription> options);


  /** Get the current settings for user-defined argument variables */
  list<UserArgVarGroupInfo> GetUserArgVarInfo(1: UserArgVarCategory category);

  /** Set the current settings for user-defined argument variables */
  void SetUserArgVarInfo(1: list<UserArgVarGroupInfo> info);

  /** Load the current settings for user-defined argument variables from a file.
      Throws a ProjectManagerError if the file cannot be imported. 
   */
  void ImportUserArgVarInfo(1: UserArgVarCategory category, 2: string argVarFilePath) throws (1:ProjectManagerError e);

  /** Save the current settings for user-defined argument variables to a file */
  void ExportUserArgVarInfo(1: UserArgVarCategory category, 2: string argVarFilePath);

  /** Get the current external tools */
  list<ExternalTool> GetExternalTools();

  /** Set the external tools to use */
  void SetExternalTools(1: list<ExternalTool> tools);
  
  /** Get the list of available plugins */
  list<WizardPlugin> GetWizards(1: string toolchainId);
  /** Create a project by running a wizard
      Throws a ProjectManagerError if the wizard failed to create a project.
  */
  ProjectContext RunWizard(1: WizardPlugin wizard) throws (1:ProjectManagerError e)

  list<OptionDescription> GetGlobalOptions();
  list<OptionDescription> GetGlobalOption(1: string id);
  list<OptionDescription> ApplyGlobalOptions(1: list<OptionDescription> options) throws (1:ProjectManagerError e);
  /** Returns a three-digit version number */
  string GetTargetVersion(1: ProjectContext prj, 2: Configuration configuration) throws (1:ProjectManagerError e);

  /** Collect the resolved aliases, if any, for the project. This maps file-to-file*/
  map<string, string> GetProjectAliases(1: ProjectContext prj);

  /** Collect the project folder aliases for the project, i.e., folder-to-folder, which can be
      used to create the file-to-file alias mapping.
  */
  map<string, string> GetProjectFolderAliases(1: ProjectContext prj);
  /** Set the project folder aliases to be used when resolving the file-to-file alias mapping.*/
  bool SetProjectFolderAliases(1: ProjectContext prj, 2: map<string, string> aliases, 3: bool forceUpdate);

  /** Check if the given configuration has a launch file specified */
  DebugLauncherInfo GetDebugLauncherInfo(1: ProjectContext prj, 2: Configuration configuration) throws (1:ProjectManagerError e);
  /** Reload the current launch file if one is provided. Throws if the reload fails or if no launch file has been specified */
  void ReloadLaunchFile(1: ProjectContext prj, 2: Configuration configuration) throws (1:ProjectManagerError e);
  /** Set the launch file to use in the project. Leave launchName empty to select the first one in launch file.*/
  void SetLaunchFile(1: ProjectContext prj, 2: Configuration configuration, 3: string launchFile, 4: string launchName, 5: bool activate) throws (1:ProjectManagerError e);

  //! If found, sets the specified CMake target as the debugger launch target.
  void SetCMakeDebugTarget(1: ProjectContext prj, 2: string target) throws (1:ProjectManagerError e);
  //! If found, gets the debugger launch CMake target, empty otherwise.
  string GetCMakeDebugTarget(1: ProjectContext prj) throws (1:ProjectManagerError e);

  //! Runs C-STAT on the specified project configuration. If successful, the results will be stored in the output build directory.
  void RunCStatOnProject(1: ProjectContext prj, 2: string configurationName, 3: bool runAsync) throws (1:ProjectManagerError e);

  //! Generate a C-STAT report on the specified project configuration.
  void GenerateCStatReport(1: ProjectContext prj, 2: string configurationName, 3: string savePath, 4: bool full) throws (1:ProjectManagerError e);

  //! Returns whether a C-STAT database exists for the specified project configuration, and in turn, whether an analysis has been performed.
  bool HasCStatDatabase(1: ProjectContext prj, 2: string configurationName) throws (1:ProjectManagerError e);

  //! Clears the C-STAT database.
  void ClearCStatDatabase(1: ProjectContext prj, 2: string configurationName) throws (1:ProjectManagerError e);

  //! Run C-STAT on the specified build item.
  void CStatAnalyze(1: BuildItem buildItem, 2: bool runAsync) throws (1:ProjectManagerError e);

  /** Returns whether the build item can be C-STAT analyzed */
  bool CanCStatAnalyze(1: BuildItem buildItem) throws (1:ProjectManagerError e);

  //! Returns whether an analysis is in progress.
  bool IsCStatRunning();

 //! Export CStat settings for the project:
 void ExportCStatSettings(1: ProjectContext prj, 2: string configurationName, 3: string filename) throws (1:ProjectManagerError e);
 //! Import CStat settings for the root node in the given project.
 void ImportCStatSettingsForProject(1: ProjectContext prj, 2: string configurationName, 3: string filename) throws (1:ProjectManagerError e);
 //! Export a CStat checks file for usage with C-STAT.
 void ExportCStatChecks(1: ProjectContext prj, 2: string configurationName, 3: string filename) throws (1:ProjectManagerError e);

 //! Export a CStat Configuration for the project:
 void ExportCStatConfiguration(1: ProjectContext prj, 2: string configurationName, 3: string filename) throws (1:ProjectManagerError e);
}
