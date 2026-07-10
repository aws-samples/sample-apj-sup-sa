import { useConversation, type TabKey } from "../store/conversation";

const TABS: { key: TabKey; label: string; icon: string; live: boolean }[] = [
  { key: "list", label: "List", icon: "📋", live: true },
  { key: "cart", label: "Cart", icon: "🛒", live: true },
  { key: "products", label: "Products", icon: "🔍", live: true },
  { key: "offers", label: "Offers", icon: "🏷️", live: true },
  { key: "recipes", label: "Recipes", icon: "🍳", live: true },
  { key: "profile", label: "Profile", icon: "👤", live: false },
];

export function TabBar() {
  const activeTab = useConversation((s) => s.activeTab);
  const tabActivity = useConversation((s) => s.tabActivity);
  const setActiveTab = useConversation((s) => s.setActiveTab);

  return (
    <div className="tabbar" role="tablist">
      {TABS.map((t) => (
        <button
          key={t.key}
          role="tab"
          aria-selected={activeTab === t.key}
          className={`tab ${activeTab === t.key ? "active" : ""} ${
            tabActivity[t.key] ? "ping" : ""
          }`}
          onClick={() => setActiveTab(t.key)}
          title={t.live ? "Live — voice-driven" : "Preview (seeded)"}
        >
          <span className="tab-icon">{t.icon}</span>
          <span className="tab-label">{t.label}</span>
          {!t.live && <span className="tab-dot" title="preview" />}
        </button>
      ))}
    </div>
  );
}
