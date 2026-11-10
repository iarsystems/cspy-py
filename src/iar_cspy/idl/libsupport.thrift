namespace cpp CSpy.LibSupport.Thrift
namespace java com.iar.cspy.libsupport

include "shared.thrift"

const string LIBSUPPORT_SERVICE = "libsupport";
const shared.ServiceVersion LIBSUPPORT_VERSION = {
    "major": 1, // Increment for breaking changes
    "minor": 0  // Increment for non-breaking changes
};

/**
 * This service redirects the I/O streams of the program being
 * debugged by C-SPY towards the environment hosting C-SPY itself.
 */
service LibSupportService2 {
    /** Get the service version hosted by the server. */
    shared.ServiceVersion GetServiceVersion();


    /**
     * Request input from the terminal I/O console.
     */
    binary requestInputBinary(1: i32 len)     
    
    /** @deprecated, use requestInputBinary instead */
    string requestInput(1: i32 len)

    /**
     * Handle output from the target program.
     */
    void printOutputBinary(1: binary data)

    /** @deprecated, use printOutputBinary instead */
    void printOutput(1: string data)
    
    /**
     * The target program has exited.
     */
    void exit(1: i32 code)
    
    /**
     * The target program has aborted (i.e. called abort()).
     */
    void reportAssert(1: string file,
                      2: string line,
                      3: string message);
}

