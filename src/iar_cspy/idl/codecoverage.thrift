namespace cpp CSpy.CodeCoverage.Thrift
namespace java com.iar.cspy.codecoverage

include "shared.thrift"

const string CODECOVERAGE_SERVICE = "codecoverage";

service CodeCoverage extends shared.HeartbeatService {

    // Return a unique identifier so that the UI can now when data originates
    // from the same session. 
    i64 getSessionId(),
        
    bool enable(1: bool enable),

    bool isEnabled(),
    
    bool hasMetaDataSupport(),
    
    void clearCachedData(),
    
    bool isInitialized(),
    
    void initializeMetaData(),
    
    bool reinitMetaData(),

    void refreshMetaData(),
    
    string getXMLData(),
}
