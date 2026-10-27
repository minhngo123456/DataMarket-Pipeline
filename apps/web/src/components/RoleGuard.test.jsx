import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import { RoleGuard } from './RoleGuard.jsx';

function renderAs(role) {
  return render(
    <MemoryRouter initialEntries={['/upload']}>
      <Routes>
        <Route element={<RoleGuard user={{ role }} allowedRoles={['admin', 'analyst']} />}>
          <Route path="/upload" element={<p>Trang upload</p>} />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

describe('RoleGuard', () => {
  it('shows 403 to a viewer', () => {
    renderAs('viewer');
    expect(screen.getByText(/403/)).toBeInTheDocument();
    expect(screen.queryByText('Trang upload')).not.toBeInTheDocument();
  });
  it('lets an analyst through', () => {
    renderAs('analyst');
    expect(screen.getByText('Trang upload')).toBeInTheDocument();
  });
});
