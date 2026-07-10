import { useState, useEffect, useCallback } from 'react'
import {
  ChefHat,
  Clock,
  Package,
  Truck,
  CheckCircle,
  RefreshCw,
  Bell,
} from 'lucide-react'

const API_BASE = import.meta.env.VITE_API_BASE || '/api'

const STATUS_FLOW = ['placed', 'confirmed', 'preparing', 'dispatched', 'in_transit', 'delivered']

const statusConfig = {
  placed: { icon: Clock, color: 'bg-yellow-100 text-yellow-800 border-yellow-200', label: 'New Order' },
  confirmed: { icon: Clock, color: 'bg-blue-100 text-blue-800 border-blue-200', label: 'Confirmed' },
  preparing: { icon: ChefHat, color: 'bg-orange-100 text-orange-800 border-orange-200', label: 'Preparing' },
  dispatched: { icon: Package, color: 'bg-purple-100 text-purple-800 border-purple-200', label: 'Dispatched' },
  in_transit: { icon: Truck, color: 'bg-indigo-100 text-indigo-800 border-indigo-200', label: 'In Transit' },
  delivered: { icon: CheckCircle, color: 'bg-green-100 text-green-800 border-green-200', label: 'Delivered' },
  cancelled: { icon: Clock, color: 'bg-red-100 text-red-800 border-red-200', label: 'Cancelled' },
}

export default function App() {
  const [orders, setOrders] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('active') // 'active' | 'all' | specific status
  const [lastRefresh, setLastRefresh] = useState(new Date())

  const fetchOrders = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/kitchen/orders`)
      const data = await res.json()
      const orderList = Array.isArray(data) ? data : data.orders || []
      setOrders(orderList)
      setLastRefresh(new Date())
    } catch (err) {
      console.error('Failed to fetch orders:', err)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    fetchOrders()
    // Poll every 10 seconds
    const interval = setInterval(fetchOrders, 10000)
    return () => clearInterval(interval)
  }, [fetchOrders])

  const updateStatus = async (orderId, newStatus) => {
    try {
      await fetch(`${API_BASE}/kitchen/orders/${orderId}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: newStatus }),
      })
      // Optimistic update
      setOrders(prev =>
        prev.map(o =>
          (o.order_id || o.id) === orderId ? { ...o, status: newStatus } : o
        )
      )
    } catch (err) {
      console.error('Failed to update status:', err)
      fetchOrders()
    }
  }

  const getNextStatus = (current) => {
    const idx = STATUS_FLOW.indexOf(current)
    if (idx >= 0 && idx < STATUS_FLOW.length - 1) {
      return STATUS_FLOW[idx + 1]
    }
    return null
  }

  const filteredOrders = orders.filter(o => {
    if (filter === 'active') return !['delivered', 'cancelled'].includes(o.status)
    if (filter === 'all') return true
    return o.status === filter
  })

  const activeCount = orders.filter(o => !['delivered', 'cancelled'].includes(o.status)).length

  return (
    <div className="min-h-screen bg-gray-900 text-white">
      {/* Header */}
      <header className="bg-gray-800 border-b border-gray-700 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <ChefHat className="w-8 h-8 text-orange-400" />
            <h1 className="text-xl font-bold">Kitchen Dashboard</h1>
            {activeCount > 0 && (
              <span className="flex items-center gap-1 bg-orange-500 text-white px-2.5 py-0.5 rounded-full text-sm font-medium">
                <Bell className="w-3.5 h-3.5" />
                {activeCount} active
              </span>
            )}
          </div>
          <div className="flex items-center gap-4">
            <span className="text-sm text-gray-400">
              Last updated: {lastRefresh.toLocaleTimeString()}
            </span>
            <button
              onClick={fetchOrders}
              className="p-2 hover:bg-gray-700 rounded-lg transition"
              title="Refresh"
            >
              <RefreshCw className="w-5 h-5 text-gray-300" />
            </button>
          </div>
        </div>
      </header>

      {/* Filters */}
      <div className="max-w-7xl mx-auto px-6 py-4">
        <div className="flex gap-2 overflow-x-auto pb-2">
          {[
            { key: 'active', label: 'Active Orders' },
            { key: 'placed', label: 'New' },
            { key: 'confirmed', label: 'Confirmed' },
            { key: 'preparing', label: 'Preparing' },
            { key: 'dispatched', label: 'Dispatched' },
            { key: 'delivered', label: 'Delivered' },
            { key: 'all', label: 'All' },
          ].map(f => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`px-4 py-2 rounded-lg text-sm font-medium whitespace-nowrap transition ${
                filter === f.key
                  ? 'bg-orange-500 text-white'
                  : 'bg-gray-800 text-gray-300 hover:bg-gray-700'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {/* Orders Grid */}
      <div className="max-w-7xl mx-auto px-6 pb-8">
        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {[...Array(6)].map((_, i) => (
              <div key={i} className="bg-gray-800 rounded-xl h-48 animate-pulse" />
            ))}
          </div>
        ) : filteredOrders.length === 0 ? (
          <div className="text-center py-16">
            <Package className="w-16 h-16 text-gray-600 mx-auto mb-4" />
            <p className="text-gray-400 text-lg">No orders in this category</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {filteredOrders.map(order => {
              const orderId = order.order_id || order.id
              const status = order.status || 'placed'
              const cfg = statusConfig[status] || statusConfig.placed
              const Icon = cfg.icon
              const nextStatus = getNextStatus(status)

              return (
                <div key={orderId} className="bg-gray-800 rounded-xl border border-gray-700 overflow-hidden">
                  {/* Order Header */}
                  <div className="p-4 border-b border-gray-700 flex items-center justify-between">
                    <div>
                      <h3 className="font-semibold text-white">Order #{orderId}</h3>
                      <p className="text-xs text-gray-400 mt-0.5">
                        {order.created_at ? new Date(order.created_at).toLocaleTimeString() : 'Just now'}
                      </p>
                    </div>
                    <span className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border ${cfg.color}`}>
                      <Icon className="w-3.5 h-3.5" />
                      {cfg.label}
                    </span>
                  </div>

                  {/* Items */}
                  <div className="p-4 space-y-2">
                    {(order.items || []).map((item, idx) => (
                      <div key={idx} className="flex justify-between text-sm">
                        <span className="text-gray-300">
                          {item.quantity}x {item.name || `Item #${item.item_id || item.menu_item_id}`}
                        </span>
                        <span className="text-gray-400">₹{item.subtotal || item.price}</span>
                      </div>
                    ))}
                    <div className="flex justify-between text-sm font-semibold pt-2 border-t border-gray-700">
                      <span className="text-white">Total</span>
                      <span className="text-orange-400">₹{order.total}</span>
                    </div>
                  </div>

                  {/* Actions */}
                  {nextStatus && (
                    <div className="p-4 border-t border-gray-700">
                      <button
                        onClick={() => updateStatus(orderId, nextStatus)}
                        className="w-full py-2.5 bg-orange-500 text-white font-medium rounded-lg hover:bg-orange-600 transition text-sm"
                      >
                        Mark as {statusConfig[nextStatus]?.label || nextStatus}
                      </button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
