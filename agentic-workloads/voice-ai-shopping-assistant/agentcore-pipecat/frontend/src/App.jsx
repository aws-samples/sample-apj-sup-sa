import React, { useCallback, useEffect, useRef, useState } from 'react';
import { DailyClient } from './daily-client.js';
import ProductCard from './components/ProductCard.jsx';
import CartPanel from './components/CartPanel.jsx';

const START_URL =
  import.meta.env.VITE_START_URL ||
  'https://<your-start-api-id>.execute-api.ap-southeast-2.amazonaws.com/prod';

export default function App() {
  const [state, setState] = useState('idle'); // idle | connecting | connected | error
  const [error, setError] = useState('');
  const [micOn, setMicOn] = useState(true);

  const [products, setProducts] = useState([]);
  const [productsTitle, setProductsTitle] = useState('');
  const [cart, setCart] = useState(null);
  const [order, setOrder] = useState(null);

  const [userText, setUserText] = useState('');
  const [agentText, setAgentText] = useState('');
  const [agentSpeaking, setAgentSpeaking] = useState(false);
  const [hasVideo, setHasVideo] = useState(false);

  const clientRef = useRef(null);
  const mediaRef = useRef(null);

  const handleBotMessage = useCallback((msg) => {
    if (!msg || !msg.type) return;
    if (msg.type === 'transcript') {
      if (msg.role === 'user') setUserText(msg.text || '');
      else if (msg.role === 'agent') {
        setAgentText(msg.text || '');
        setAgentSpeaking(!msg.final);
      }
      return;
    }
    if (msg.type === 'tool_result') {
      const data = msg.data || {};
      switch (msg.tool) {
        case 'search_products':
          setProducts(data.products || []);
          setProductsTitle('Results');
          break;
        case 'get_product_variants':
          setProducts(data.variants || []);
          setProductsTitle('Compare options');
          break;
        case 'add_to_cart':
        case 'get_cart':
          if (data.cart) setCart(data.cart);
          break;
        case 'create_order':
          if (data.order) setOrder(data.order);
          break;
        default:
          break;
      }
    }
  }, []);

  useEffect(() => () => clientRef.current?.disconnect(), []);

  const connect = async () => {
    setState('connecting');
    setError('');
    try {
      const client = new DailyClient(START_URL);
      clientRef.current = client;

      client.on('track', (stream) => {
        if (mediaRef.current && mediaRef.current.srcObject !== stream) {
          mediaRef.current.srcObject = stream;
        }
        setHasVideo(stream.getVideoTracks().length > 0);
      });
      client.on('connectionStateChange', (s) => {
        if (s === 'connected') setState('connected');
        else if (s === 'disconnected') endSession();
      });
      client.on('error', (e) => {
        setError(e.message);
        setState('error');
      });
      client.on('botMessage', handleBotMessage);

      await client.initializeLocalMedia();
      await client.connect();
    } catch (e) {
      setError(e.message || 'Failed to start');
      setState('error');
    }
  };

  const endSession = () => {
    clientRef.current?.disconnect();
    clientRef.current = null;
    setState('idle');
    setProducts([]);
    setCart(null);
    setOrder(null);
    setUserText('');
    setAgentText('');
  };

  const toggleMic = () => {
    const next = !micOn;
    setMicOn(next);
    clientRef.current?.toggleMicrophone(next);
  };

  // --- Start screen ---
  if (state === 'idle' || state === 'error') {
    return (
      <div className="start">
        <div className="start-card">
          <p className="eyebrow">Voice grocery assistant</p>
          <h1>Aisle</h1>
          <p className="lede">
            Talk to Aisle to find groceries, compare brands and specials, build a
            cart, and place a pickup order — all by voice, across the Aisle range.
          </p>
          <button className="btn-primary" onClick={connect}>Start shopping</button>
          {state === 'error' && <p className="error">{error}</p>}
          <ul className="hints">
            <li>“Find me some milk.”</li>
            <li>“What pasta is on special?”</li>
            <li>“Add the cheapest one to my cart.”</li>
            <li>“What's in my cart? Place a pickup order.”</li>
          </ul>
        </div>
      </div>
    );
  }

  // --- Session ---
  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">Aisle</div>
        <div className="agent-tile">
          <video
            ref={mediaRef}
            autoPlay
            playsInline
            className={hasVideo ? 'agent-video' : 'agent-video hidden'}
          />
          {!hasVideo && (
            <div className={`orb ${agentSpeaking ? 'speaking' : ''}`} aria-hidden />
          )}
          <span className="agent-label">
            {state === 'connecting' ? 'Connecting…' : agentSpeaking ? 'Aisle speaking' : 'Listening'}
          </span>
        </div>
        <div className="controls">
          <button className={`btn-mic ${micOn ? '' : 'muted'}`} onClick={toggleMic}>
            {micOn ? 'Mute' : 'Unmute'}
          </button>
          <button className="btn-end" onClick={endSession}>End</button>
        </div>
      </header>

      <main className="content">
        <section className="products">
          {products.length > 0 ? (
            <>
              <h2 className="section-title">{productsTitle}</h2>
              <div className="product-grid">
                {products.map((p) => (
                  <ProductCard key={p.product_id || p.name} product={p} />
                ))}
              </div>
            </>
          ) : (
            <div className="empty-state">
              <p>🛒</p>
              <p>Ask Aisle to find something — “show me some milk”.</p>
            </div>
          )}
        </section>

        <CartPanel cart={cart} order={order} />
      </main>

      <footer className="transcript">
        <div className="t-row">
          <span className="t-label">You</span>
          <span className="t-text">{userText || '…'}</span>
        </div>
        <div className="t-row">
          <span className="t-label agent">Aisle</span>
          <span className="t-text">{agentText || '…'}</span>
        </div>
      </footer>
    </div>
  );
}
