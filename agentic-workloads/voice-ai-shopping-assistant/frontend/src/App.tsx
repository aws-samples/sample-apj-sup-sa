import { useConversation, type TabKey } from "./store/conversation";
import { VoiceOrb } from "./components/VoiceOrb";
import { AgentVideo } from "./components/AgentVideo";
import { TranscriptPanel } from "./components/TranscriptPanel";
import { MicControl } from "./components/MicControl";
import { TabBar } from "./components/TabBar";
import { CartPanel } from "./components/panels/CartPanel";
import { ProductsPanel } from "./components/panels/ProductsPanel";
import { RecipePanel } from "./components/panels/RecipePanel";
import { OffersPanel } from "./components/panels/OffersPanel";
import { GroceryListPanel } from "./components/panels/GroceryListPanel";
import { ProfilePanel } from "./components/panels/ProfilePanel";

const PANELS: Record<TabKey, () => JSX.Element> = {
  cart: CartPanel,
  products: ProductsPanel,
  recipes: RecipePanel,
  offers: OffersPanel,
  list: GroceryListPanel,
  profile: ProfilePanel,
};

export default function App() {
  const connection = useConversation((s) => s.connection);
  const errorMessage = useConversation((s) => s.errorMessage);
  const activeTab = useConversation((s) => s.activeTab);
  const hasAvatar = useConversation((s) => s.agentVideoStream !== null);
  const Panel = PANELS[activeTab];

  return (
    <div className="app">
      <header className="app-header">
        <span className="brand">Aisle</span>
        <span className={`conn ${connection}`}>
          <i className="dot" />
          {connection === "connected"
            ? "connected"
            : connection === "connecting"
            ? "connecting…"
            : connection === "error"
            ? "error"
            : "offline"}
        </span>
      </header>

      <div className="layout">
        <section className="stage">
          <div className={`stage-media ${hasAvatar ? "has-avatar" : "has-orb"}`}>
            {hasAvatar ? <AgentVideo /> : <VoiceOrb />}
            <TranscriptPanel />
          </div>
          <MicControl />
          {errorMessage && <div className="error-banner">{errorMessage}</div>}
        </section>

        <section className="workspace">
          <TabBar />
          <div className="panel-host">
            <Panel />
          </div>
        </section>
      </div>
    </div>
  );
}
