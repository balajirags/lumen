import { useOrderCount } from './hooks';

function formatLabel(count) {
  return `Orders: ${count}`;
}

export function OrderWidget(props) {
  const count = useOrderCount();
  const label = formatLabel(count);
  return <div onClick={() => props.onRefresh()}>{label}</div>;
}
