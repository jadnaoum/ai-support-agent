import { Navigate, Route, Routes } from 'react-router-dom'
import SetupPage from '@/pages/SetupPage'
import ChatPage from '@/pages/ChatPage'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<SetupPage />} />
      <Route path="/chat/:conversationId" element={<ChatPage />} />
      <Route path="/replay/:conversationId" element={<ChatPage readOnly />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
