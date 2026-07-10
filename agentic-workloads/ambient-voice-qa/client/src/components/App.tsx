import { useEffect } from 'react';

import type { PipecatBaseChildProps } from '@pipecat-ai/voice-ui-kit';
import {
  ConnectButton,
  ConversationPanel,
  UserAudioControl,
} from '@pipecat-ai/voice-ui-kit';

import type { TransportType } from '../config';
import { ChecklistPanel } from './ChecklistPanel';
import { TransportSelect } from './TransportSelect';

interface AppProps extends PipecatBaseChildProps {
  transportType: TransportType;
  onTransportChange: (type: TransportType) => void;
  availableTransports: TransportType[];
}

export const App = ({
  client,
  handleConnect,
  handleDisconnect,
  transportType,
  onTransportChange,
  availableTransports,
}: AppProps) => {
  useEffect(() => {
    client?.initDevices();
  }, [client]);

  const showTransportSelector = availableTransports.length > 1;

  return (
    <div className="flex flex-col w-full h-full">
      <div className="flex items-center justify-between gap-4 p-4 border-b border-gray-800">
        <div className="flex items-baseline gap-3">
          <div className="text-xl font-bold text-emerald-400">
            Ambient Voice QA
          </div>
          <div className="text-xs uppercase tracking-widest text-gray-500">
            Manufacturing Inspection — hands-free
          </div>
        </div>
        <div className="flex items-center gap-4">
          {showTransportSelector && (
            <TransportSelect
              transportType={transportType}
              onTransportChange={onTransportChange}
              availableTransports={availableTransports}
            />
          )}
          <UserAudioControl size="lg" />
          <ConnectButton
            size="lg"
            onConnect={handleConnect}
            onDisconnect={handleDisconnect}
          />
        </div>
      </div>
      <div className="flex-1 grid grid-cols-1 lg:grid-cols-2 gap-4 p-4 overflow-hidden">
        <div className="h-full overflow-hidden flex flex-col">
          <div className="text-xs uppercase tracking-widest text-gray-500 mb-2">
            Live Transcript
          </div>
          <div className="flex-1 overflow-hidden border border-gray-700 rounded-md bg-black/40">
            <ConversationPanel />
          </div>
        </div>
        <div className="h-full overflow-hidden">
          <ChecklistPanel />
        </div>
      </div>
    </div>
  );
};
