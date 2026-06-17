import React from 'react';
import { render } from '@testing-library/react-native';
import WelcomeScreen from '../../screens/WelcomeScreen';

describe('WelcomeScreen', () => {
  it('renders correctly', () => {
    const tree = render(<WelcomeScreen />);
    expect(tree).toMatchSnapshot();
  });
});
