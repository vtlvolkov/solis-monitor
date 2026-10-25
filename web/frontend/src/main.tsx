import { render } from 'preact';
import { App } from './App';
import { applyColorTokens } from './lib/tokens';
import './styles.css';

applyColorTokens();
render(<App />, document.getElementById('app')!);
