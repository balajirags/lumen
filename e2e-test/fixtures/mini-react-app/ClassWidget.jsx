class ClickCounter extends Component {
  handleClick = () => {
    this.increment();
  };

  increment() {
    this.setState({});
  }

  render() {
    return <button onClick={this.handleClick}>Click</button>;
  }
}
