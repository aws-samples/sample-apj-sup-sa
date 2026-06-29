import type { MeetingWorkspaceProps } from '../meeting/MeetingWorkspace';
import MeetingWorkspace from '../meeting/MeetingWorkspace';

function QuickMeetingView(props: MeetingWorkspaceProps) {
  return <MeetingWorkspace {...props} />;
}

export default QuickMeetingView;
