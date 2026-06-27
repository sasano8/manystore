
class UserDictLike(dict):
    def __init__(self, name: str, age: int):
        super().__init__(
            name=name,
            age=age,
        )

a = UserDictLike(name="Alice", age=10)
print(a)
