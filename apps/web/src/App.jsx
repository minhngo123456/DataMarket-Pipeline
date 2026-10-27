import { Authenticator } from '@aws-amplify/ui-react';
import { BrowserRouter } from 'react-router-dom';
import { isCognitoConfigured } from './aws-config.js';
import { AppRoutes } from './components/AppRoutes.jsx';

function ConfigurationHelp() {
  return (
    <main className="app-shell">
      <p className="eyebrow">MARKETDATA PIPELINE</p>
      <h1>Chưa tìm thấy cấu hình Cognito</h1>
      <p className="intro">
        Tạo file <code>apps/web/.env.local</code> từ <code>.env.example</code>,
        điền User Pool ID và App client ID, rồi khởi động lại Vite.
      </p>
    </main>
  );
}

export default function App() {
  if (!isCognitoConfigured) {
    return <ConfigurationHelp />;
  }

  return (
    <div className="auth-shell">
      {/* Accounts are created by an admin (Cognito AllowAdminCreateUserOnly), so self sign-up is hidden. */}
      <Authenticator loginMechanisms={['email']} hideSignUp>
        {({ user, signOut }) => (
          <BrowserRouter>
            <AppRoutes cognitoUser={user} signOut={signOut} />
          </BrowserRouter>
        )}
      </Authenticator>
    </div>
  );
}
