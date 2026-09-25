namespace cpp OptionsService.Thrift
namespace java com.iar.optionsservice

include "shared.thrift"
include "projectmanager.thrift"


//
// Constants
//

// Service identifier
const string SERVICE_ID = "com.iar.optionsservice";

const string BUILD_ACTION_BUILD_SEQUENCE_OPTION_ID = "BUILDACTION.BuildActions.BuildSequence";
const string BUILD_ACTION_COMMAND_LINE_OPTION_ID = "BUILDACTION.BuildActions.CommandLine";
const string BUILD_ACTION_DEPENDENCIES_OPTION_ID = "BUILDACTION.BuildActions.Dependencies";
const string BUILD_ACTION_OUTPUTS_OPTION_ID = "BUILDACTION.BuildActions.Outputs";
const string BUILD_ACTION_WORKING_DIRECTORY_OPTION_ID = "BUILDACTION.BuildActions.WorkingDirectory";

exception OptionsServiceError {
  1: string description;
}


//
// Data types
//

struct CreateSessionRequest
{
    1: string projectPath;
    2: string configurationName;
    4: string nodePathOrIndex;
    3: bool showHiddenOptions;
}

struct CreateSessionResponse
{
    1: shared.Id sessionId;
    2: shared.Success success;
    3: projectmanager.ProjectContext context;
    4: bool readOnly;
}

struct DestroySessionRequest
{
    1: shared.Id sessionId;
}

struct DestroySessionResponse
{
    1: shared.Id sessionId;
    2: shared.Success success;
}

struct Tree
{
    1: shared.Id id;
    2: string data;
}

struct GetCategoryTreeRequest
{
    1: shared.Id sessionId;
}

struct GetCategoryTreeResponse
{
    1: shared.Id sessionId;
    2: Tree tree;
    3: shared.Success success;
}

struct GetOptionTreeRequest
{
    1: shared.Id sessionId;
    2: shared.Id treeId;
}

struct GetOptionTreeResponse
{
    1: shared.Id sessionId;
    2: Tree tree;
    3: shared.Success success;
}

struct OptionValue
{
    1: string optionDefinitionId;
    2: string data;
    3: list<OptionValue> children;
    /** Whether the value is supposed to be inherited from parent */
    4: bool inherited;
}

struct VerificationError
{
    1: string optionDefinitionId;
    2: string errorMessage;
}

struct UpdateOptionsStateRequest
{
    1: shared.Id sessionId;
    2: shared.Id treeId;
    3: list<OptionValue> createdOptionValues;
    4: list<OptionValue> updatedOptionValues;
    5: list<OptionValue> deletedOptionValues;
}

struct UpdateOptionsStateResponse
{
    1: shared.Id sessionId;
    2: Tree tree;
    3: shared.Success success;
    4: list<VerificationError> verificationErrors;
    /** True when this change altered which option categories are visible
     *  (per the OptionsService category-visibility rules). The client should
     *  re-request GetCategoryTree and structurally rebuild the category
     *  navigation. Defaults to false. */
    5: bool categoryTreeInvalidated;
}

struct CommitOptionStateRequest
{
    1: shared.Id sessionId;
}

struct CommitOptionStateResponse
{
    1: shared.Id sessionId;
    2: shared.Success success;
}


//
// Services
//

service OptionsService
{
    CreateSessionResponse CreateSession(1: CreateSessionRequest request);
    DestroySessionResponse DestroySession(1: DestroySessionRequest destroySessionRequest);
    GetCategoryTreeResponse GetCategoryTree(1: GetCategoryTreeRequest getCategoryTreeRequest);
    GetOptionTreeResponse GetOptionTree(1: GetOptionTreeRequest getOptionTreeRequest);
    UpdateOptionsStateResponse UpdateOptionsState(1: UpdateOptionsStateRequest updateOptionsStateRequest);
    CommitOptionStateResponse CommitOptionState(1: CommitOptionStateRequest commitOptionStateRequest);
}
