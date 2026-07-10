import { useState, useEffect } from 'react'
import { Package, Clock, Truck, CheckCircle } from 'lucide-react'
import { getCurrentOrder, getDeliveryStatus } from '../api'

const statusConfig = {
  placed: { icon: Clock, color: 'text-yellow-500', bg: 'bg-yellow-50', label: 'Order Placed' },
  confirmed: { icon: Clock, color: 'text-blue-500', bg: 'bg-blue-50', label: 'Confirmed' },
  preparing: { icon: Package, color: 'text-orange-500', bg: 'bg-orange-50', label: 'Preparing' },
  dispatched: { icon: Truck, color: 'text-purple-500', bg: 'bg-purple-50', label: 'Dispatched' },
  in_transit: { icon: Truck, color: 'text-blue-600', bg: 'bg-blue-50', label: 'On the Way' },
  delivered: { icon: CheckCircle, color: 'text-green-500', bg: 'bg-green-50', label: 'Delivered' },
  cancelled: { icon: Clock, color: 'text-red-500', bg: 'bg-red-50', label: 'Cancelled' },
}

export default function OrdersPage() {
  const [order, setOrder] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    fetchOrder()
  }, [])

  const fetchOrder = async () => {
    try {
      const data = await getCurrentOrder()
      setOrder(data)
    } catch (err) {
      setError('No active orders found')
    }
    setLoading(false)
  }

  if (loading) return <div className="text-center py-12 text-gray-500">Loading orders...</div>

  if (error || !order) {
    return (
      <div className="text-center py-12">
        <Package className="w-16 h-16 text-gray-300 mx-auto mb-4" />
        <h2 className="text-xl font-semibold text-gray-600 mb-2">No Active Orders</h2>
        <p className="text-gray-400">Place an order to see it here</p>
      </div>
    )
  }

  const status = order.status || 'placed'
  const cfg = statusConfig[status] || statusConfig.placed
  const Icon = cfg.icon

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-800 mb-6">Your Order</h1>

      {/* Status Card */}
      <div className={`${cfg.bg} rounded-xl p-6 mb-4`}>
        <div className="flex items-center gap-3 mb-2">
          <Icon className={`w-6 h-6 ${cfg.color}`} />
          <span className={`font-semibold text-lg ${cfg.color}`}>{cfg.label}</span>
        </div>
        <p className="text-sm text-gray-600">Order #{order.order_id || order.id}</p>
        {order.eta_minutes && (
          <p className="text-sm text-gray-500 mt-1">Estimated delivery in {order.eta_minutes} minutes</p>
        )}
      </div>

      {/* Progress Bar */}
      <div className="bg-white rounded-xl border p-6 mb-4">
        <div className="flex items-center justify-between mb-2">
          {['placed', 'confirmed', 'preparing', 'dispatched', 'delivered'].map((s, i) => {
            const steps = ['placed', 'confirmed', 'preparing', 'dispatched', 'delivered']
            const currentIdx = steps.indexOf(status)
            const isCompleted = i <= currentIdx
            return (
              <div key={s} className="flex flex-col items-center">
                <div className={`w-8 h-8 rounded-full flex items-center justify-center ${
                  isCompleted ? 'bg-orange-500 text-white' : 'bg-gray-200 text-gray-400'
                }`}>
                  {i + 1}
                </div>
                <span className="text-xs text-gray-500 mt-1 capitalize">{s}</span>
              </div>
            )
          })}
        </div>
      </div>

      {/* Order Items */}
      <div className="bg-white rounded-xl border divide-y">
        <div className="p-4 font-semibold text-gray-700">Order Items</div>
        {(order.items || []).map((item, idx) => (
          <div key={idx} className="flex items-center justify-between p-4">
            <div>
              <h3 className="font-medium text-gray-800">{item.name || `Item #${item.item_id}`}</h3>
              <p className="text-sm text-gray-500">x{item.quantity}</p>
            </div>
            <span className="font-semibold">₹{item.subtotal || item.price}</span>
          </div>
        ))}
        <div className="flex items-center justify-between p-4 bg-gray-50">
          <span className="font-semibold text-gray-700">Total</span>
          <span className="text-lg font-bold text-gray-800">₹{order.total}</span>
        </div>
      </div>
    </div>
  )
}
