import { useState, useEffect } from 'react';

type Status = 'online' | 'offline' | 'reconnected';

export default function NetworkStatus() {
  const [status, setStatus] = useState<Status>(
    typeof navigator !== 'undefined' && navigator.onLine ? 'online' : 'offline',
  );

  useEffect(() => {
    const handleOffline = () => setStatus('offline');
    const handleOnline = () => {
      setStatus('reconnected');
      setTimeout(() => setStatus('online'), 3000);
    };

    window.addEventListener('offline', handleOffline);
    window.addEventListener('online', handleOnline);

    return () => {
      window.removeEventListener('offline', handleOffline);
      window.removeEventListener('online', handleOnline);
    };
  }, []);

  if (status === 'online') {
    return null;
  }

  if (status === 'offline') {
    return (
      <div className="fixed top-0 left-0 right-0 z-50 bg-amber-50 border-b border-amber-200 px-4 py-2.5 text-center">
        <p className="text-sm text-amber-800">
          网络连接已断开，请检查网络设置
        </p>
      </div>
    );
  }

  if (status === 'reconnected') {
    return (
      <div className="fixed top-0 left-0 right-0 z-50 bg-green-50 border-b border-green-200 px-4 py-2.5 text-center">
        <p className="text-sm text-green-800">网络已恢复</p>
      </div>
    );
  }

  return null;
}
