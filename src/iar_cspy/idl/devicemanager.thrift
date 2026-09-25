namespace cpp DeviceManager.Thrift
namespace java com.iar.devicemanager

include "shared.thrift"

const string DEVICEMANAGER_ID = "com.iar.devicemanager.service"

/**
 * Generic error thrown by the device service.
 */ 
exception DeviceManagerError {
	1	: string detailMessage;
}

/**
 * The possible states of a device.
 */
enum DeviceState
{
	/** Device not installed locally, but available for install */
	Available = 0,
	/** Device installed locally and ready for use */
	Installed = 1,
	/** Device not compatible with current installation */
	Incompatible = 2
}

/**
 */
struct Device
{
	/** A unique string identifying this device, for example "Default"
	 *
	 * In a traditional EW installation, instances of this object match "*.menu" files on disk.
   * Device tags are supposed to be unique to a given toolchain, and are compared in a case-insensitive fashion.
	 * Searching for "default" tag will yield the "Default" device.
	 */
	1: string tag;

	/** A user-readable display name for this device, for example "None"
	 *
	 * These are potentially localized, for example when using "*.ENU.menu" files.
	 */
	2: string displayName;

	/** A user-readable string identifying the device manufacturer (e.g. "ST")
	 * 
	 * Note that other classifiers as device family are stored as part of the device's path attribute
	 */
	3: string manufacturer

	/** The relative path of the device .menu file from the repository root
	 * Devices will be organized in a consistent structure following different classifiers,
	 * such as " <manufacturer> / <device family> / <device sub-family> / <device .menu file> "
	 * 
	 */
	4: string path;

	/** A string containing the target-dependent data for this device.
	 * This will usually point to another target-specific file (e.g. .i79 files for arm, or the menu file itself).
	 * However, its intepretation is left to the target to decide.
	 */
	5: string data;

	/** Whether this device is installed and ready for use, needs to be downloaded, cannot be used, etc. */
	6: DeviceState state;

	/** The id of the EW toolchain this device is to be used with, e.g. 'arm' */
	7: string toolchainId
}

/**
 * Describes the possible repository variants
 */
enum RepositoryType
{
	/** Repository type is unknown */
	Unknown = 0,
	/** The repository is available locally in the machine running the application */
	Local = 1,
	/** The repository is available remotely, through a connection protocol specified by its URI */
	Remote = 2
}


/**
 */
struct Repository
{
	/** Unique URI for this repository
	 *  Might be e.g. a file:// URI for local repositories, or an https:// URI for remote ones
	 */
	1: string uri;
	/** User-readable name of this repository, e.g. "EWARM local installation" */
	2: string displayName;
	/** The unique ID of the toolchain which the devices in this repository should be used with (e.g. 'arm', 'riscv', etc.) */
	3: string toolchainId;
	/** The type of this repository (local, remote, ...) */
	4: RepositoryType type;
}

/**
 * 
 *
 */
service DeviceManager
{
	/**
	 * Gets a list of all known repositories for a given IAR toolchain id (e.g. 'arm')
   *
	 * @throws DeviceManagerError in case the repository list cannot be retrieved
	 */
	list<Repository> getRepositoriesForToolchain(1: string toolchainId) throws (1:DeviceManagerError e);
	
	/**
	 * Gets a map of device tags to device definitions given a Repository
	 *
	 * @throws DeviceManagerError in case the list of devices for this repository's URI and toolchain cannot be retrieved.
	 * Clients calling this function should handle this exception according to the repository type.
   * For example, remote repositories can be subjected to timeout and other connection errors.
	 */
  map<string, Device> getDevicesForRepository(1: Repository repository) throws (1: DeviceManagerError e);

	/**
	 * Add a Repository to the device manager
	 *
	 * @throws DeviceManagerError if the repository definition is invalid or cannot be registered
	 */
  void addRepository(1: Repository repository) throws (1: DeviceManagerError e);

	/**
	 * Add a Repository to the device manager, using a local file path
	 *
	 * @returns the Repository which was added to the device manager
	 * @throws DeviceManagerError if the repository definition is invalid or cannot be registered
	 */
  Repository addRepositoryForLocalPath(1: string localPath, 2: string toolchainId, 3: string displayName) throws (1: DeviceManagerError e);

	/**
	 * Remove a repository from the device manager for the given URI and toolchain.
	 *
	 * This is a no-op if the repository has not been registered previously.
	 */
  void removeRepositoryForUri(1: string uri, 2: string toolchainId);

	/**
	 * Remove a repository from the device manager, accounting for its URI and toolchain.
	 *
	 * This is a no-op if the repository has not been registered previously.
	 */
  void removeRepository(1: Repository repo);


	/**
	 * Query the project manager for the currently loaded toolchains, and add
	 * repositories for each one of them.
	 *
	 * Currently this adds the $TOOLKIT_DIR$/config/devices folder for each toolchain
	 * if no additional metadata is found.
	 *
	 * TBD: toolchain metadata format to specify which repos should be added
	 3
	 */
	map<string, list<Repository>> addRepositoriesFromToolchains()


}

