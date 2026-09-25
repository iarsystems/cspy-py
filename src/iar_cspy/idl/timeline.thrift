namespace cpp Timeline.Thrift
namespace java com.iar.timeline

include "ServiceRegistry.thrift"
include "shared.thrift"

const string TIMELINE_FRONTEND_SERVICE = "timeline.frontend";

// Data source access service hosted by the backend
const string TIMELINE_BACKEND_SERVICE = "timeline.backend";

// FRONTEND SERVICE TYPES ////////////

/**
 * Sent to notify of new data being available in all the channels whose id matches the 'channelId' regexp
 */
struct DbuTimelineDataAvailableNotification
{
  1: string channelId;
  2: i64 startCycles;
  3: i64 endCycles;
  4: bool dummy;
}

/**
 * Sent to notify of a new data channel being available
 */
struct DbuTimelineChannelAvailableNotification
{
  1: string id;
  2: string formatDescriptor;
}

/**
 * Sent to notify of a data channel having been removed
 */
struct DbuTimelineChannelRemovedNotification
{
  1: string id;
  2: optional string formatDescriptor;
}

/**
 * Sent to notify that the CPU frequency has changed
 */
struct DbuTimelineCpuClockChangedNotification
{
  1: i64 cyclesPerSecond;
}

/**
 * Sent to notify that the enablement state of all the channels matching the provided id regexp has changed
 */
struct DbuTimelineEnablementChangedNotification
{
  1: string channelId;
  2: bool enabled;
}

/**
 * Listener service for timeline notifications, hosted by the frontend. As we only run a single frontend per session, we must include the partnerId in the communication.
 */
service TimelineFrontend
{
  void dataAvailable(1: DbuTimelineDataAvailableNotification note, 2: string partnerNamespace);
  void channelAvailable(1: DbuTimelineChannelAvailableNotification note, 2: string partnerNamespace);
  void channelRemoved(1: DbuTimelineChannelRemovedNotification note, 2: string partnerNamespace);
  void cpuClockChanged(1: DbuTimelineCpuClockChangedNotification note, 2: string partnerNamespace);
  void enablementChanged(1: DbuTimelineEnablementChangedNotification note, 2: string partnerNamespace);
}


struct TimelineChannelInfo {
    1: string id;
    2: string formatDescriptor;
}

/**
 * Service for retrieving Timeline data from the backend
 */
service TimelineBackend extends shared.HeartbeatService
{  
    list<TimelineChannelInfo> getChannels();
    i64 getCPUClock();
    
    // FIXME mariopi: use something more efficient than string to transmit data!
    string readData(1: string id, 2: i64 startTime, 3: i64 endTime);
    string readOverflows(1: string id, 2: i64 startTime, 3: i64 endTime);    
    oneway void enable(1: string id, 2: bool enabled);
    bool isEnabled(1: string id);        
}
